import pandas as pd
import numpy as np
from typing import Optional

from pathlib import Path
import locale
import datetime
import math

from ledgercli.bankformat import BankFormat


class Ledger:
    def __init__(self, output_path: Path, export_path: Optional[Path], bank_format: Optional[str]):

        self.current_output_dir = output_path
        self.current_bank_fornat = bank_format

        mapping_schema = {
            "recipient_clean": pd.Series(dtype=str),
            "label1": pd.Series(dtype=str),
            "label2": pd.Series(dtype=str),
            "label3": pd.Series(dtype=str),
            "occurence": pd.Series(dtype=int),
        }
        transaction_schema = {
            "amount_custom": pd.Series(dtype=float),
            "date_custom": pd.Series(dtype=object),
            "recipient_clean_custom": pd.Series(dtype=str),
            "label1_custom": pd.Series(dtype=str),
            "label2_custom": pd.Series(dtype=str),
            "label3_custom": pd.Series(dtype=str),
            "occurence_custom": pd.Series(dtype=int),
        }
        self.transactions = pd.DataFrame(
            {
                "amount": pd.Series(dtype=float),
                "date": pd.Series(dtype=object),
                "recipient": pd.Series(dtype=str),
                **mapping_schema,
                **transaction_schema,
            }
        )
        self.transactions_coalesced = pd.DataFrame(
            {
                "amount": pd.Series(dtype=float),
                "date": pd.Series(dtype=object),
                **mapping_schema,
            }
        )
        self.transactions_distributed = self.transactions_coalesced.copy()

        self.mappings = pd.DataFrame({"recipient": pd.Series(dtype=str), **mapping_schema})
        self.metadata = pd.DataFrame(
            {
                "starting_balance": pd.Series(dtype=float),
                "bank_format": pd.Series(dtype=str),
            }
        )
        self.history = pd.DataFrame(
            {
                "date": pd.Series(dtype=object),
                "amount": pd.Series(dtype=float),
                "balance": pd.Series(dtype=float),
            }
        )

        self.udpate(export_path=export_path, bank_format=bank_format)

    def udpate(self, export_path: Optional[Path], bank_format: Optional[str]) -> None:
        self.init_ledger(export_path, bank_format)
        self.init_metadata(export_path, bank_format)
        self.init_mappingtable()
        self.update_mappings()
        self.init_coalesced_ledger()
        self.init_distributed_ledger()
        self.init_history()
        self._create_date_columns()
        self._create_type_column()

    def init_ledger(self, export_path: Optional[Path], bank_format: Optional[str]) -> None:
        """Reads existing ledger and appends optional export.

        :param export_path: path to your banking export.
        :param bank_format: specify which bank format you're using.

        Reads current ledger in output_dir and then tries to append export using the specified bank_format.
        """
        tmp_ledger = self.current_output_dir / "ledger.csv"

        if tmp_ledger.exists() is True:
            tmp = pd.read_csv(tmp_ledger, parse_dates=["date", "date_custom"])
            self.transactions = pd.concat([self.transactions, tmp])

        if export_path is not None and bank_format is not None and export_path.exists():
            tmp = BankFormat.get_transactions(bank_format=bank_format, export_path=export_path)
            tmp.columns = ["date", "recipient", "amount"]
            self.transactions = pd.concat([self.transactions, tmp])

    def init_metadata(self, export_path: Optional[Path], bank_format: Optional[str]) -> None:
        """Calculates starting balance from export.

        :param export_path: path to your banking export.
        :param bank_format: specify which bank format you're using.

        TODO set metadata if current ledger empty?
        """
        tmp_metadata = self.current_output_dir / "metadata.csv"
        if tmp_metadata.exists() is True:
            tmp = pd.read_csv(tmp_metadata)
            self.metadata = pd.concat([self.metadata, tmp])

        elif export_path is not None and bank_format is not None and export_path.exists():
            end_balance = BankFormat.get_end_balance(bank_format=bank_format, export_path=export_path)
            revenue = self.transactions["amount"].sum()
            self.metadata = pd.DataFrame(
                {
                    "starting_balance": [float(end_balance - revenue)],
                    "bank_format": [bank_format],
                }
            )

    def init_mappingtable(self) -> None:
        """Reads mapping table and appends new recipients.

        Reads current mappingtable and then creates two sets of transactions (tr) and mappingtables (mp) recipients.
        The difference (tr - mp) is added to mappingtable.

        If a ledger transaction gets deleted their mappingtable record is kept.
        """

        tmp_maptab = self.current_output_dir / "mappingtable.csv"

        if tmp_maptab.exists() is True:
            tmp = pd.read_csv(tmp_maptab)
            self.mappings = pd.concat([self.mappings, tmp])

        ledger_recipients = set(self.transactions["recipient"].unique())
        mp_recipients = set(self.mappings["recipient"].unique())
        new_mappings = pd.DataFrame(ledger_recipients.difference(mp_recipients), columns=["recipient"])

        self.mappings = pd.concat([self.mappings, new_mappings], ignore_index=True)

    def update_mappings(self) -> None:
        """Re-maps transactions.

        Drops label1-3, recipient_clean and occurence from transactions and merges
        mappingtable after.
        """
        tmp_transactions = self.transactions.copy()
        tmp_mp = self.mappings.copy()

        tmp = tmp_transactions[
            tmp_transactions.columns.difference(["label1", "label2", "label3", "recipient_clean", "occurence"])
        ].merge(tmp_mp, how="left", on="recipient")
        self.transactions = tmp

    def init_coalesced_ledger(self) -> None:
        """Coalesces all custom values."""
        tmp = self.transactions.copy()

        for col in [
            "date",
            "amount",
            "occurence",
            "recipient_clean",
            "label1",
            "label2",
            "label3",
        ]:
            tmp[f"{col}_custom"].update(tmp[col])
            tmp = tmp.drop(f"{col}_custom", axis=1)
        tmp = tmp.drop("recipient", axis=1).rename(columns={"recipient_clean": "recipient"})
        self.transactions_coalesced = tmp

    def init_history(self) -> None:
        """Calculate daily balance.

        Coalesced transaction are grouped by date and a cumulative sum is generated starting at
        the starting_balance.
        """
        if self.transactions_coalesced.empty is False:
            tmp_history = self.transactions_coalesced.groupby(["date"], as_index=False)["amount"].sum()
            tmp_history["balance"] = tmp_history["amount"].cumsum() + self.metadata["starting_balance"].iloc[0]

            self.history = pd.concat([self.history, tmp_history], ignore_index=True)

    def _create_date_columns(self) -> None:
        """Helper function to generate week, month, quarter and year timestamps from each date."""
        for df in [self.transactions, self.transactions_coalesced, self.transactions_distributed, self.history]:
            for col in ["week", "month", "quarter", "year"]:
                df[col] = pd.to_datetime(df["date"]).dt.to_period(col.upper()[0]).dt.to_timestamp()

    def _create_type_column(self) -> None:
        """Create type record for all coalesced tables."""
        for df in [
            self.transactions_coalesced,
            self.transactions_distributed,
            self.history,
        ]:
            df["type"] = np.where(df["amount"] > 0, "Income", "Expense")

    def init_distributed_ledger(self) -> None:
        """Distributes coalesced transactions basd on occurence.

        TODO indexing can be probably more concise, check for null and 0 at the same time
        """
        na_index = pd.isna(self.transactions_coalesced["occurence"])
        repeat_tmp = self.transactions_coalesced[~na_index]
        rest = self.transactions_coalesced[na_index]

        repeat = repeat_tmp[repeat_tmp["occurence"] != 0]
        rest2 = repeat_tmp[repeat_tmp["occurence"] == 0]

        rest = pd.concat([rest, rest2], ignore_index=True)

        if repeat.empty is False:
            repeat["date"] = repeat.apply(
                lambda x: pd.date_range(
                    start=x.date if x.occurence > 0 else None,
                    end=x.date if x.occurence < 0 else None,
                    periods=abs(x.occurence),
                    freq="MS",
                ),
                axis=1,
            )

            repeat = repeat.explode("date")
            repeat["amount"] = repeat["amount"] / abs(repeat["occurence"])
            repeat["occurence"] = np.where(repeat["occurence"] > 0, 1, -1)

        self.transactions_distributed = pd.concat([rest, repeat], ignore_index=True)

    def write(self) -> None:
        """Writes all internal formats to current output_path."""

        write_map = dict(
            {
                "ledger": self.transactions,
                "metadata": self.metadata,
                "mappingtable": self.mappings,
                "history": self.history,
                "transactions_coalesced": self.transactions_coalesced,
                "transactions_distributed": self.transactions_distributed,
            }
        )

        for k, v in write_map.items():
            v.to_csv(
                self.current_output_dir / f"{k}.csv",
                index=False,
                date_format="%Y-%m-%d",
                float_format="%.2f",
            )
