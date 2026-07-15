TEXT = {
    "en": {
        "session": "Session", "weekly": "Weekly", "failed": "Request failed", "settings": "Settings",
        "session_title": "Session (5h)", "weekly_title": "Weekly (7d)",
        "resets": "Resets {time}", "refresh_interval": "Refresh interval", "seconds": "s",
        "launch_at_login": "Launch on startup", "language": "Language", "back": "Back",
        "menu_panel": "Panel", "menu_settings": "Settings", "menu_messages": "Messages", "menu_exit": "Exit",
        "reset_alerts": "Reset alerts", "loading": "Loading…", "no_reset_alerts": "No reset posts in the last 7 days.",
        "feed_unavailable": "Network unavailable.",
        "retry": "Refresh",
    },
    "zh": {
        "session": "会话", "weekly": "每周", "failed": "请求失败", "settings": "设置",
        "session_title": "会话 (5小时)", "weekly_title": "每周 (7天)",
        "resets": "{time} 重置", "refresh_interval": "刷新间隔", "seconds": "秒",
        "launch_at_login": "开机自启动", "language": "语言", "back": "返回",
        "menu_panel": "面板", "menu_settings": "设置", "menu_messages": "消息", "menu_exit": "退出",
        "reset_alerts": "重置提醒", "loading": "正在加载…", "no_reset_alerts": "近 7 天没有包含 reset 的推文。",
        "feed_unavailable": "网络不可用。",
        "retry": "重新获取",
    },
}


def text(language: str, key: str) -> str:
    return TEXT.get(language, TEXT["en"]).get(key, key)
