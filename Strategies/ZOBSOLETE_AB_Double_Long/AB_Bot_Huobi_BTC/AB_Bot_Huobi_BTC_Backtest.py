from AB_Bot.AB_Bot_Huobi_BTC.API_initiation import API_initiation

from AB_Bot.Backtest_AB import backtest_ab


if __name__ == "__main__":
    API = API_initiation()

    backtest_ab(API, Base = "BTC")
