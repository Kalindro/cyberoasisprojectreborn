from AB_Bot.AB_Bot_Kucoin_ETH.API_initiation import API_initiation

from AB_Bot.Backtest_AB_auto import backtest_ab_auto


if __name__ == "__main__":
    API = API_initiation()

    backtest_ab_auto(API, Base = "ETH")