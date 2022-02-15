import backtrader as bt

from datetime import date, datetime
import numpy as np
from scipy.stats import linregress

import pandas as pd
from pandas import DataFrame as df

import collections
from beepy import beep

import functools
import multiprocessing

from Gieldy.Refractor_general.Get_filtered_tickers import get_filtered_tickers
from Gieldy.Refractor_general.Get_history import get_history_full

# Parameter settings
OPTIMIZE = True
SAVE_EXCEL = True

FRESH_LIVE_TICKERS = False
FORBID_EXCHANGE_TOKENS = False
BLACK_LIST_ENABLE = False
TICKERS_MODE = 2  # 1 = One coin 2 = Few coins 3 = Whole exchange
TIMEFRAME = "1H"
THE_ONE_COIN = ["DOCK/USDT"]

if BLACK_LIST_ENABLE:
    BLACK_LIST = ["SNX/USDT", "UTK/USDT", "COS/USDT", "CTXC/USDT", "AION/USDT", "BEL/USDT", "MTL/USDT", "EOS/USDT", "WTC/USDT", "LTC/USDT",
                  "KMD/USDT", "LRC/USDT", "ARDR/USDT", "AKRO/USDT", "BCH/USDT", "BAT/USDT", "WAN/USDT", "BTS/USDT",
                  "KEY/USDT", "GTO/USDT", "ONT/USDT"]
else:
    BLACK_LIST = []

BASIC_ORDER_LOG = False
DETAILED_ORDER_LOG = False
TRADE_LOG = False

if OPTIMIZE:
    BASIC_ORDER_LOG = False
    DETAILED_ORDER_LOG = False
    TRADE_LOG = False


# Backtrader classes
class CommInfoFractional(bt.CommissionInfo):
    def getsize(self, price, cash):
        return self.p.leverage * (cash / price)


class PandasData(bt.feeds.PandasData):
    params = (
        ("datetime", None),
        ("open", "open"),
        ("high", "high"),
        ("low", "low"),
        ("close", "close"),
        ("volume", "volume"),
        ("openinterest", None),
    )


class PortfolioAnalyzer(bt.Analyzer):
    def next(self):
        self.portfolio_ending = self.strategy.broker.get_value()

    def get_analysis(self):
        return self.portfolio_ending


class ExposureObserver(bt.Observer):
    alias = ("ExposureObserver",)
    lines = ("exposure",)

    plotinfo = dict(plot=True, subplot=True)

    def next(self):
        value = self._owner.broker.getvalue()
        self.lines.exposure[0] = round((1 - self._owner.broker.getcash() / value) * 100, 2)


class MyBuySell(bt.observers.BuySell):
    plotinfo = dict(plot=True, subplot=False, plotlinelabels=True)
    plotlines = dict(
        buy=dict(marker="^", markersize=10.0, color="royalblue"),
        sell=dict(marker="v", markersize=10.0, color="darkorchid")
    )


class KeltnerChannel(bt.Indicator):
    lines = ('top', 'sma', 'bot',)
    params = dict(sma_period=0, atr_period=0, atr_fix=0)
    plotlines = dict(
        top=dict(color="steelblue"),
        sma=dict(color="red"),
        bot=dict(color="steelblue"))
    plotinfo = dict(subplot=False)

    def __init__(self):
        self.lines.sma = bt.indicators.SMA(self.data, period=self.p.sma_period)
        atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
        self.lines.top = self.lines.sma + (self.p.atr_fix / 10) * atr
        self.lines.bot = self.lines.sma - (self.p.atr_fix / 10) * atr


class Momentum(bt.ind.PeriodN):
    lines = ("momentum", "rvalue")
    params = dict(period=0, rvalue_filter=0)
    plotinfo = dict(plot=False)

    def next(self):
        returns_fast = np.log(self.data.get(size=int(self.params.period * 0.5)))
        returns_slow = np.log(self.data.get(size=int(self.params.period)))
        x_fast = np.arange(len(returns_fast))
        x_slow = np.arange(len(returns_slow))
        slope_fast, _, rvalue_fast, _, _ = linregress(x_fast, returns_fast)
        slope_slow, _, rvalue_slow, _, _ = linregress(x_slow, returns_slow)
        rvalue_fast = 1 - rvalue_fast
        rvalue_slow = 1 - rvalue_slow
        momentum_fast = slope_fast * (rvalue_fast ** 2) * 10000
        momentum_slow = slope_slow * (rvalue_slow ** 2) * 10000
        self.lines.momentum[0] = round((momentum_fast + momentum_slow) / 2, 2)
        self.lines.rvalue[0] = round(((rvalue_fast ** 2) + (rvalue_slow ** 2)) / 2, 4)


class KeltnerStrategy(bt.Strategy):
    def log(self, txt):
        dt = self.datas[0].datetime.datetime(0)
        print("%s, %s" % (dt.isoformat(), txt))

    params = dict(
        ATR_period=0,
        ATR_fix=0,
        keltner_period=0,
        momentum_period=0,
        rvalue_filter=0,
        ETH_SMA_period=0,
        ATRR_period=0,
    )

    def __init__(self):
        self.total_percent_pnl = 0
        self.ETH = self.datas[0]
        self.coins_indicators = collections.defaultdict(dict)
        self.WIGGLED_ROOM = 0.95
        self.MOMENTUM_FILTER = 0
        self.NUM_ELITE_COINS = 25

        for d in self.datas[1:]:
            curr_momentum = Momentum(d, period=self.params.momentum_period, rvalue_filter=self.params.rvalue_filter)
            self.coins_indicators[d]["keltner"] = KeltnerChannel(d, sma_period=self.params.keltner_period, atr_period=self.params.ATR_period,
                                                                 atr_fix=self.params.ATR_fix, plotname="Keltner Channel")
            self.coins_indicators[d]["momentum"] = curr_momentum.lines.momentum
            self.coins_indicators[d]["ATRR"] = 1 / (bt.indicators.ATR(d, period=self.params.ATRR_period) / d.close)
            self.coins_indicators[d]["perc_pnl"] = 0
            self.coins_indicators[d]["trades_number"] = 0

    def notify_trade(self, trade):
        if trade.isclosed:
            self.total_percent_pnl += (trade.pnlcomm / self.broker.get_value() * 100)
            self.coins_indicators[trade.data]["perc_pnl"] += (trade.pnlcomm / self.broker.get_value() * 100)
            self.coins_indicators[trade.data]["trades_number"] += 1

        if TRADE_LOG:
            self.log(
                f"PNL: ${trade.pnlcomm:.2f}\t"
                f"Coin: {trade.data._name}\t"
            )

    def notify_order(self, order):
        base_logging = (f"Type: {('BUY' if order.isbuy() else 'SELL')}\t"
                        f"Status: {order.getstatusname()}\t"
                        f"Order size: {order.size:,.4f}\t"
                        f"Create Price: {order.created.price:,.4f}\t"
                        f"Order cost: {order.size * order.created.price:,.2f}\t"
                        f"Cash: {self.broker.get_cash():,.2f}\t"
                        f"Acc Value: {self.broker.get_value():,.2f}\t"
                        f"Position: {self.getposition(order.data).size:,.4f}\t"
                        f"Coin: {order.data._name}\t"
                        )

        if order.status in [order.Margin, order.Rejected]:
            self.log(
                f"Order {order.ref} Margin/Rejected\t" + base_logging
            )

        if BASIC_ORDER_LOG and (order.status in [order.Completed]):
            self.log(
                f"EXECUTED {order.ref}\t" + base_logging
            )

        if DETAILED_ORDER_LOG:
            self.log(
                f"Order {order.ref}\t" + base_logging
            )

    def prenext(self):
        self.coins = [d for d in self.datas[1:] if len(d)]
        self.next()

    def nextstart(self):
        self.coins = self.datas[1:]
        self.next()

    def next(self):
        # Cancel all orders
        [self.cancel(o) for o in self.broker.get_orders_open() if o.status < 4]

        self.all_coins_loop_list = list(filter(lambda x: len(x) > max(self.p.momentum_period, self.p.ETH_SMA_period, self.p.ATRR_period), self.coins))
        self.coins_ranking = df(columns=["data", "pair", "momentum", "ATRR", "perc_pnl"])
        for d in self.all_coins_loop_list:
            pair = d._name
            pair_momentum = self.coins_indicators[d]["momentum"][0]
            pair_atrr = self.coins_indicators[d]["ATRR"][0]
            pair_perc_pnl = self.coins_indicators[d]["perc_pnl"]
            if (pair_momentum < 0) or (pair in BLACK_LIST): continue
            ranking_dict = {"data": [d], "pair": [pair], "momentum": [pair_momentum], "ATRR": [pair_atrr], "perc_pnl": [pair_perc_pnl]}
            ziobroooo_ty = pd.DataFrame.from_dict(ranking_dict)
            self.coins_ranking = self.coins_ranking.append(ziobroooo_ty, ignore_index=True)
        self.coins_ranking.sort_values(by=["momentum"], inplace=True, ascending=False)
        self.coins_ranking.reset_index(inplace=True, drop=True)
        self.elite_length = len(self.coins_ranking.head(self.NUM_ELITE_COINS))
        self.ATRR_elite_sum = self.coins_ranking.head(self.NUM_ELITE_COINS)["ATRR"].sum()

        self.backtest_pnl = df(columns=["pair", "perc_pnl", "trades_number"])
        for d in self.coins_indicators:
            pair = d._name
            pair_perc_pnl = self.coins_indicators[d]["perc_pnl"]
            pair_trades_number = self.coins_indicators[d]["trades_number"]
            ranking_dict = {"pair": [pair], "perc_pnl": [pair_perc_pnl], "trades_number": [pair_trades_number]}
            ziobroooo_ty = pd.DataFrame.from_dict(ranking_dict)
            self.backtest_pnl = self.backtest_pnl.append(ziobroooo_ty, ignore_index=True)
        self.backtest_pnl.sort_values(by=["perc_pnl"], inplace=True, ascending=False)

        # Sell coins section
        for d in self.all_coins_loop_list:
            sell_conditions = (d not in self.coins_ranking["data"].head(self.NUM_ELITE_COINS).values)
            if self.getposition(d) and sell_conditions:
                self.close(d, exectype=bt.Order.Market)

        # Buy coins section
        for d in self.coins_ranking["data"].head(self.NUM_ELITE_COINS).values:
            if not self.getposition(d):
                playing_value = self.broker.get_value() * (self.elite_length / (self.NUM_ELITE_COINS + 1))
                weight = round((self.coins_indicators[d]["ATRR"] / self.ATRR_elite_sum), 6)
                target_size = playing_value * weight / d.close[0]
                self.buy(d, exectype=bt.Order.Limit, price=self.coins_indicators[d]["keltner"].lines.bot[0], size=target_size)

            if self.getposition(d):
                self.close(d, exectype=bt.Order.Limit, price=self.coins_indicators[d]["keltner"].lines.top[0])

    def stop(self):
        for d in self.coins_indicators:
            pos = self.getposition(d)
            comminfo = self.broker.getcommissioninfo(d)
            pnl = comminfo.profitandloss(pos.size, pos.price, d.close[0])
            self.total_percent_pnl += (pnl / self.broker.get_value() * 100)
        self.coins_ranking[["pair", "momentum", "ATRR", "perc_pnl"]].to_excel("Ranking Backtrader.xlsx")
        self.backtest_pnl.to_excel("Ranking Backtrader PNL.xlsx")
        print(f"Compounded {self.total_percent_pnl:.2f}% profit")
        print(f"Run ${self.broker.getvalue():,.2f} complete")


def backtest_AB_l(API, base, start=date.fromisoformat("2021-01-01"), end=date.fromisoformat("2021-11-22")):
    name = API["name"]

    # Indicators periods
    ATR_PERIOD = tuple((400, ))
    ATR_FIX = tuple((25, ))
    KELTNER_PERIOD = tuple(range(1, 10, 2))
    MOMENTUM_PERIOD = tuple((400, ))
    ATRR_PERIOD = tuple((110, ))

    if not OPTIMIZE:
        ATR_PERIOD = ATR_PERIOD[0]
        KELTNER_PERIOD = KELTNER_PERIOD[0]
        ATR_FIX = ATR_FIX[0]
        MOMENTUM_PERIOD = MOMENTUM_PERIOD[0]
        ATRR_PERIOD = ATRR_PERIOD[0]

    if not OPTIMIZE:
        cerebro = bt.Cerebro(live=False, exactbars=False, stdstats=False, runonce=True, preload=True, optreturn=True, optdatas=True,
                             quicknotify=True, maxcpus=10)
    else:
        cerebro = bt.Cerebro(live=False, exactbars=True, stdstats=False, runonce=False, preload=True, optreturn=True, optdatas=True,
                             quicknotify=True, maxcpus=10)

    # Get tickers
    if TICKERS_MODE == 1:
        tickers = THE_ONE_COIN

    elif TICKERS_MODE == 2:
        tickers = ["ETH/USDT", "LTC/USDT", "NEO/USDT", "EOS/USDT", "XRP/USDT", "BAT/USDT", "TRX/USDT", "ADA/USDT", "BNB/USDT", "BTC/USDT",
                   "LINK/USDT", "VET/USDT", "WING/USDT"]

    elif TICKERS_MODE == 3:
        tickers = get_filtered_tickers(base=base, API=API, fresh_live_tickers=FRESH_LIVE_TICKERS,
                                       forbid_exchange_tokens=FORBID_EXCHANGE_TOKENS)

    # Add the data to Cerebro
    partial_history = functools.partial(get_history_full, start=start, end=end, fresh_live_history=False, timeframe=TIMEFRAME, API=API)

    shark_pool = multiprocessing.Pool(processes=6)
    all_coins_history = shark_pool.map(partial_history, tickers)
    shark_pool.close()
    shark_pool.join()

    plot = True if TICKERS_MODE == 1 else False
    ETH_full_history = get_history_full(pair="ETH/USDT", start=start, end=end, fresh_live_history=False, timeframe=TIMEFRAME, API=API)
    ETH_data_ready = PandasData(dataname=ETH_full_history, name="ETH/USDT", plot=not plot)
    cerebro.adddata(ETH_data_ready)

    for coin_history in all_coins_history:
        if OPTIMIZE:
            if len(coin_history) > max(max(ATR_PERIOD), max(KELTNER_PERIOD)):
                cerebro.adddata(PandasData(dataname=coin_history, plot=plot, name=str(coin_history["pair"].iloc[0])))
        if not OPTIMIZE:
            if len(coin_history) > max(ATR_PERIOD, KELTNER_PERIOD):
                cerebro.adddata(PandasData(dataname=coin_history, plot=plot, name=str(coin_history["pair"].iloc[0])))

    print("Got history for all the coins")

    # Change buy/sell arrows type and color
    if not OPTIMIZE and not TICKERS_MODE == 3:
        cerebro.addobservermulti(MyBuySell, barplot=False, bardist=0)

    # Add observers
    cerebro.addobserver(bt.observers.Value)
    cerebro.addobserver(bt.observers.DrawDown)
    cerebro.addobserver(ExposureObserver, _name="exposure_observer")

    # Add analyzers
    cerebro.addanalyzer(bt.analyzers.TimeReturn, data=ETH_data_ready, timeframe=bt.TimeFrame.NoTimeFrame, _name="benchmark_TR")
    cerebro.addanalyzer(bt.analyzers.TimeReturn, timeframe=bt.TimeFrame.NoTimeFrame, _name="strat_TR")
    cerebro.addanalyzer(bt.analyzers.DrawDown)
    cerebro.addanalyzer(bt.analyzers.SQN)
    cerebro.addanalyzer(PortfolioAnalyzer, _name="portfolio_analyzer")

    # Set our desired cash start
    starting_cash = 10_000
    cerebro.broker.setcash(starting_cash)
    cerebro.broker.setcommission(commission=0.00100)
    cerebro.broker.addcommissioninfo(CommInfoFractional())
    cerebro.broker.set_checksubmit(False)

    # Add strategy
    if OPTIMIZE:
        cerebro.optstrategy(KeltnerStrategy, ATR_period=ATR_PERIOD, keltner_period=KELTNER_PERIOD,
                            ATR_fix=ATR_FIX, momentum_period=MOMENTUM_PERIOD, ATRR_period=ATRR_PERIOD)
    else:
        cerebro.addstrategy(KeltnerStrategy, ATR_period=ATR_PERIOD, keltner_period=KELTNER_PERIOD,
                            ATR_fix=ATR_FIX, momentum_period=MOMENTUM_PERIOD, ATRR_period=ATRR_PERIOD)

    results = cerebro.run()

    strats = [x[0] for x in results] if OPTIMIZE else results
    all_runs_dataframe = df()
    for i, strat in enumerate(strats):
        Run_data = {
            "Run number": [i],
            "End value": round(strat.analyzers.portfolio_analyzer.get_analysis(), 2),
            "End perc PnL": round(strat.total_percent_pnl, 2) if not OPTIMIZE else 0,
            "Max DD": round(strat.analyzers.drawdown.get_analysis()["max"]["drawdown"], 2),
            "Value": round(strat.analyzers.portfolio_analyzer.get_analysis() / (strat.analyzers.drawdown.get_analysis()["max"]["drawdown"] / 100), 2) if
            strat.analyzers.drawdown.get_analysis()["max"]["drawdown"] > 0 else 0,
            "Perc value": round(strat.total_percent_pnl / (strat.analyzers.drawdown.get_analysis()["max"]["drawdown"] / 100), 2) if
            strat.analyzers.drawdown.get_analysis()["max"]["drawdown"] > 0 and not OPTIMIZE else 0,
            "SQN": round(strat.analyzers.sqn.get_analysis()["sqn"], 4),
            "Strat TR": round(list(strat.analyzers.strat_TR.get_analysis().values())[0], 2),
            "Bench TR": round(list(strat.analyzers.benchmark_TR.get_analysis().values())[0], 2),
            "Trades": strat.analyzers.sqn.get_analysis()["trades"],
        }
        run_parameters = vars(strat.params)
        Run_data.update(run_parameters)
        print("")
        [print(f"{k}: {v}") for k, v in Run_data.items()]
        ziobro_ty_kurwo = pd.DataFrame.from_dict(Run_data, dtype=float)
        all_runs_dataframe = all_runs_dataframe.append(ziobro_ty_kurwo, ignore_index=True)

    if SAVE_EXCEL:
        # Excel save
        basic_file_name = f"{name}"

        if OPTIMIZE:
            basic_file_name = f"OPTI {basic_file_name}, {MOMENTUM_PERIOD[0]} to {MOMENTUM_PERIOD[-1]} Mom"
        else:
            basic_file_name = f"{basic_file_name}, {MOMENTUM_PERIOD} Mom"

        addition_name = f"St {start} En {end}, {datetime.now().strftime('%Y-%m-%d %H@%M')}"

        writer = pd.ExcelWriter(f"{basic_file_name}, {addition_name}.xlsx", engine="xlsxwriter")
        workbook = writer.book

        all_runs_dataframe.to_excel(writer)

        # Excel formatting
        results_sheet = writer.sheets["Sheet1"]
        currency_format = workbook.add_format({"num_format": "$#,##0.00"})
        percent_format = workbook.add_format({"num_format": "0.00##\%"})
        number_format = workbook.add_format({"num_format": "#,##0.00"})
        results_sheet.set_column("B:P", 13)
        results_sheet.set_column("C:C", 13, currency_format)
        results_sheet.set_column("D:E", 13, percent_format)
        results_sheet.set_column("F:F", 13, number_format)
        results_sheet.set_column("H:H", 9)
        results_sheet.set_column("K:K", 9)
        results_sheet.set_column("L:P", 18)
        writer.save()

    if not OPTIMIZE:
        cerebro.plot(style="candlestick", volume=False, barup="limegreen", bardown="tomato")

    print("BEEPING")
    beep(sound=1)