import statistics
import traceback
import typing as tp
from functools import partial

import pandas as pd
from pandas import DataFrame as df
from talib import NATR

from exchange.base_functions import get_pairs_prices
from exchange.get_history import GetFullHistoryDF
from exchange.select_mode import FundamentalSettings
from utils.log_config import ConfigureLoguru
from utils.utils import excel_save_formatted

logger = ConfigureLoguru().info_level()


class _BaseSettings(FundamentalSettings):
    def __init__(self):
        self.EXCHANGE_MODE: int = 1
        self.PAIRS_MODE: int = 3
        super().__init__(exchange_mode=self.EXCHANGE_MODE, pairs_mode=self.PAIRS_MODE)

        self.TIMEFRAME = "1h"
        self.NUMBER_OF_LAST_CANDLES = 1000
        self.VOL_QUANTILE_DROP = 0.60
        self.DAYS_WINDOWS = [1, 2, 3, 7, 14, 31]
        self.MIN_VOL_USD = 300_000
        self.MIN_VOL_BTC = self._min_vol_BTC

    @property
    def _min_vol_BTC(self):
        BTC_price = get_pairs_prices(self.API).loc["BTC/USDT"]["price"]
        return self.MIN_VOL_USD / BTC_price

    @property
    def _min_data_length(self):
        return max(self.DAYS_WINDOWS) * 24


class PerformanceRankAnalysis(_BaseSettings):
    """Main analysis class"""

    def main(self) -> None:
        """Main function runin the analysis"""
        try:
            performance_calculation_results = self._calculate_performances_on_list()
            full_performance_df = self._performances_list_to_clean_df(performance_calculation_results)

            if self.PAIRS_MODE == 4:
                market_median_performance = full_performance_df["median_performance"].median()
                BTC_median_performance = full_performance_df.loc[
                    full_performance_df["pair"] == "BTC/USDT", "median_performance"].iloc[-1]
                ETH_median_performance = full_performance_df.loc[
                    full_performance_df["pair"] == "ETH/USDT", "median_performance"].iloc[-1]
                print(f"\033[93mMarket median performance: {market_median_performance:.2%}\033[0m")
                print(f"\033[93mBTC median performance: {BTC_median_performance:.2%}\033[0m")
                print(f"\033[93mETH median performance: {ETH_median_performance:.2%}\033[0m")

            excel_save_formatted(full_performance_df, filename="performance.xlsx", global_cols_size=13,
                                 cash_cols="E:F", cash_cols_size=17, rounded_cols="D:D", perc_cols="G:N",
                                 perc_cols_size=16)
            logger.success("Saved excel, all done")

        except Exception as err:
            logger.error(f"Error on main market performance, {err}")
            print(traceback.format_exc())

    def _calculate_performances_on_list(self) -> list[dict]:
        """Calculate performance on all pairs on provided list"""
        vbt_history = GetFullHistoryDF(pairs_list=self.pairs_list, timeframe=self.TIMEFRAME,
                                       number_of_last_candles=self.NUMBER_OF_LAST_CANDLES, API=self.API,
                                       min_data_length=self._min_data_length).get_full_history()

        logger.info("Calculating performance for all the coins...")
        partial_performance_calculations = partial(_PerformanceCalculation().performance_calculations,
                                                   days_windows=self.DAYS_WINDOWS, min_vol_usd=self.MIN_VOL_USD,
                                                   min_vol_btc=self.MIN_VOL_BTC)

        performances_calculation_results = [partial_performance_calculations(pair, pair_history) for pair, pair_history
                                            in vbt_history.data.items()]

        return performances_calculation_results

    def _performances_list_to_clean_df(self, performance_calculation_results: list[dict]) -> pd.DataFrame:
        """Process the list of performances and output in formatted dataframe"""
        full_performance_df = df()
        for pair_results in performance_calculation_results:
            full_performance_df = pd.concat([df(pair_results), full_performance_df], ignore_index=True)

        fast_history = full_performance_df["avg_vol_fast"]
        slow_history = full_performance_df["avg_vol_slow"]
        fast_history_quantile = fast_history.quantile(self.VOL_QUANTILE_DROP)
        slow_history_quantile = slow_history.quantile(self.VOL_QUANTILE_DROP)
        full_performance_df = full_performance_df[
            (fast_history >= fast_history_quantile) & (slow_history >= slow_history_quantile)]
        logger.success(f"Dropped bottom {self.VOL_QUANTILE_DROP * 100}% volume coins")
        full_performance_df.sort_values(by="vol_increase", ascending=False, inplace=True)

        return full_performance_df

    def performance_calculations(self, pair: str, coin_history_df: pd.DataFrame, days_windows: list[int],
                                 min_vol_usd: int, min_vol_btc: int) -> tp.Union[dict, None]:
        """Calculation all the needed performance metrics for the pairs list"""
        price = coin_history_df.iloc[-1]["Close"]
        days_history_dict = {f"{days}d_hourly_history": coin_history_df.tail(24 * days) for days in days_windows}
        fast_history = days_history_dict["3d_hourly_history"]
        slow_history = days_history_dict["31d_hourly_history"]
        avg_vol_fast = (fast_history["Volume"].sum() / int(len(fast_history) / 24)) * price
        avg_vol_slow = (slow_history["Volume"].sum() / int(len(slow_history) / 24)) * price
        vol_increase = avg_vol_fast / avg_vol_slow

        if pair.endswith(("/USDT", ":USDT")):
            min_vol = min_vol_usd
        elif pair.endswith(("/BTC", ":BTC")):
            min_vol = min_vol_btc
        else:
            raise ValueError("Invalid pairs_list quote currency: " + pair)
        if avg_vol_slow < min_vol:
            logger.info(f"Skipping {pair}, not enough volume")
            return

        coin_NATR = NATR(close=fast_history["Close"], high=fast_history["High"], low=fast_history["Low"],
                         timeperiod=len(fast_history))[-1]

        full_performance_dict = {"pair": [pair], "natr": [coin_NATR], "avg_vol_fast": [avg_vol_fast],
                                 "avg_vol_slow": [avg_vol_slow], "vol_increase": [vol_increase]}
        price_change_dict = {
            f"{days}d_performance": self._calculate_price_change(days_history_dict[f"{days}d_hourly_history"])
            for days in days_windows}
        price_change_dict["median_performance"] = statistics.median(price_change_dict.values())
        full_performance_dict.update(price_change_dict)

        return full_performance_dict

    @staticmethod
    def _calculate_price_change(cut_history_dataframe: pd.DataFrame) -> float:
        """Function counting price change in %"""
        performance = (cut_history_dataframe.iloc[-1]["Close"] - cut_history_dataframe.iloc[0]["Close"]) / \
                      cut_history_dataframe.iloc[0]["Close"]

        return performance


if __name__ == "__main__":
    PerformanceRankAnalysis().main()
