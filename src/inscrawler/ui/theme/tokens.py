"""Semantic colors shared by the light and dark desktop themes."""


def theme_tokens(theme_name: str) -> dict[str, str]:
    current_theme = theme_name
    if current_theme == "dark":
        return {
            "window": "#22201d",
            "surface": "#2d2a27",
            "base": "#171614",
            "alternate": "#36322e",
            "button": "#3b3632",
            "text": "#ece7de",
            "muted": "#b7afa4",
            "border": "#4f4943",
            "accent": "#6d97a2",
            "accent_hover": "#82a9b3",
            "accent_pressed": "#587d86",
            "link": "#82a9b3",
            "highlight": "#4d737d",
            "highlight_text": "#ffffff",
            "preview": "#2d2a27",
            "scroll_track": "#2c2926",
            "scroll_handle": "#6a645d",
            "scroll_hover": "#807970",
            "scroll_pressed": "#989087",
            "danger": "#ef7a70",
            "warning": "#f0a04c",
            "titlebar_text": "#ece7de",
        }
    return {
        "window": "#e7dfd5",
        "surface": "#f3ede5",
        "base": "#fbf8f3",
        "alternate": "#ede5db",
        "button": "#efe7dd",
        "text": "#2f2a24",
        "muted": "#74695c",
        "border": "#d4c7b7",
        "accent": "#547a86",
        "accent_hover": "#476973",
        "accent_pressed": "#395661",
        "link": "#395661",
        "highlight": "#c8d9de",
        "highlight_text": "#1f2629",
        "preview": "#f3ede5",
        "scroll_track": "#e1d8cd",
        "scroll_handle": "#9c8f80",
        "scroll_hover": "#897c6e",
        "scroll_pressed": "#716456",
        "danger": "#c2584d",
        "warning": "#b8742a",
        "titlebar_text": "#2f2a24",
    }
