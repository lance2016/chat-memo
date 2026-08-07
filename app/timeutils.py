import datetime as dt


def aware(value: dt.datetime) -> dt.datetime:
    """补上时区，缺失时按 UTC。

    Postgres 的 ``timestamptz`` 取出来一定带时区，测试用的 SQLite 会把它抹掉。
    凡是拿库里的时间做减法或比较的地方都要先过这一道 —— 少了它会 TypeError，
    而在提醒那条链路上，异常被 ticker 兜住之后表现为「这条提醒没响」，很难查。
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def local_day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """把「某一天」按**本地时区**换算成 UTC 的左闭右开区间。

    时间戳都以 UTC 存，但「今天的对话」对用户是本地概念。在 UTC+8 直接按 UTC 切天，
    会漏掉本地 00:00–08:00 的对话，还会把第二天凌晨的算进来。

    注意容器默认时区是 UTC，会让这个函数失效 —— compose 里必须设 TZ。
    """
    start_local = dt.datetime.combine(day, dt.time.min).astimezone()
    end_local = dt.datetime.combine(
        day + dt.timedelta(days=1), dt.time.min
    ).astimezone()
    return start_local.astimezone(dt.UTC), end_local.astimezone(dt.UTC)
