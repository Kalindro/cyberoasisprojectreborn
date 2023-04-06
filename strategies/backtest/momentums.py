import numpy as np
import pandas as pd
import vectorbtpro as vbt
from loguru import logger
from scipy.stats import linregress
from time import perf_counter

class MomentumAllocation:
    def allocation_momentum_ranking(self,
                                    vbt_data: vbt.Data,
                                    momentum_period: int,
                                    NATR_period: int,
                                    top_decimal: float = None,
                                    top_number: int = None) -> pd.DataFrame:
        if not (top_decimal or top_number):
            raise ValueError("Please provide either top decimal or top number")
        if not top_number:
            top_number = int(len(vbt_data.data) * top_decimal)

        momentum_data = self._momentum_calc_for_vbt_data(vbt_data=vbt_data, momentum_period=momentum_period)
        ranked_df = momentum_data.rank(axis=1, method="max", ascending=False)

        natr_data = vbt_data.run("talib_NATR", timeperiod=NATR_period).real
        natr_data.columns = natr_data.columns.droplevel(0)
        inv_vol_data = 1 / natr_data

        top_pairs = ranked_df <= top_number
        top_pairs_inv_vol = inv_vol_data.where(top_pairs, other=np.nan)
        sum_top_pairs_inv_vol = top_pairs_inv_vol.sum(axis=1)
        allocations = top_pairs_inv_vol.div(sum_top_pairs_inv_vol, axis=0).replace(0, np.nan)

        return allocations

    def _momentum_calc_for_vbt_data(self, vbt_data: vbt.Data, momentum_period: int) -> dict[str: pd.DataFrame]:
        """Calculate momentum ranking for list of history dataframes"""
        logger.info("Calculating momentum ranking for pairs histories")
        returns = np.log(vbt_data.get("Close"))
        x = np.arange(len(returns))
        CORR = vbt.IF.from_expr("@out_corr:rolling_corr_nb(@in_x, @in_y, @p_window)")
        OLS_rvalue = CORR.run(x, returns, momentum_period).corr
        OLS = vbt.OLS.run(x=x, y=returns, window=momentum_period)
        OLS_slope = OLS.slope * 100
        mom1 = OLS_slope * (OLS_rvalue ** 2)
        mom2 = vbt_data.close.rolling(momentum_period).apply(self._momentum_calculate)

        print("OLSY")
        print(mom1)
        print("JA")
        print(mom2)

        return mom1

    def _momentum_calculate(self, price_closes: pd.DataFrame) -> float:
        """Calculating momentum from close"""
        returns = np.log(price_closes)
        x = np.arange(len(returns))
        slope, _, rvalue, _, _ = linregress(x, returns)
        momentum = slope * 100
        return momentum * (rvalue ** 2)  # return (((np.exp(slope) ** 252) - 1) * 100) * (rvalue**2)
