"""
Visualization: K-line + MACD.
"""
import mplfinance as mpf
import pandas as pd


def get_macd_colors(hist):
    return ['g' if v >= 0 else 'r' for v in hist]


def plot_weekly(df, title='BTCUSDT Weekly K-Line', last_n=157):
    df_plot = df.tail(last_n)

    # Get the date range and append it to the title
    start_date = df_plot.index[0].strftime('%Y-%m-%d')
    end_date = df_plot.index[-1].strftime('%Y-%m-%d')
    title_with_range = f"{title}\n{start_date} ~ {end_date}"

    macd_colors = get_macd_colors(df_plot['hist'])

    apds = [
        mpf.make_addplot(df_plot['macd'], panel=2, color='#1f77b4'),
        mpf.make_addplot(df_plot['signal'], panel=2, color='#ff7f0e'),
        mpf.make_addplot(df_plot['hist'], type='bar', color=macd_colors, panel=2),
    ]

    out_path = f"/tmp/btc_weekly_{start_date}_{end_date}.png"
    mpf.plot(
        df_plot,
        type='candle',
        style='charles',
        title=title_with_range,
        ylabel='Price',
        volume=True,
        mav=(7, 25, 99),
        addplot=apds,
        panel_ratios=(4, 1, 2),
        figsize=(14, 10),
        savefig=out_path
    )
    print(f"Image saved: {out_path}")
