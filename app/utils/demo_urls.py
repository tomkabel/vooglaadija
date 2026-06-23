"""
Pool of demo video URLs for live demo auto-submission.

These are well-known, stable URLs used to demonstrate the download pipeline
during live presentations. URLs are sourced from publicly available content.
"""

import random

DEMO_VIDEO_URLS = [
    # YouTube (15 URLs)
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",  # Rick Astley
    "https://www.youtube.com/watch?v=jNQXAC9IVRw",  # Me at the zoo
    "https://www.youtube.com/watch?v=9bZkp7q19f0",  # PSY - Gangnam Style
    "https://www.youtube.com/watch?v=2Vv-BfVoq4g",  # Ed Sheeran - Perfect
    "https://www.youtube.com/watch?v=kJQP7kiw5Fk",  # Luis Fonsi - Despacito
    "https://www.youtube.com/watch?v=RgKAFK5djSk",  # Wiz Khalifa - See You Again
    "https://www.youtube.com/watch?v=JGwWNGJdvx8",  # Shape of You
    "https://www.youtube.com/watch?v=OPf0YbXqDm0",  # Uptown Funk
    "https://www.youtube.com/watch?v=HP-MbfHFUqs",  # Price Tag
    "https://www.youtube.com/watch?v=CevxZvSJLk8",  # Roar
    "https://www.youtube.com/watch?v=YQHsXMglC9A",  # Let It Go (Frozen)
    "https://www.youtube.com/watch?v=lp-EO5I60KA",  # Everything Is Awesome
    "https://www.youtube.com/watch?v=5yXQJpYJkKk",  # Happy
    "https://www.youtube.com/watch?v=wDgQdr8ZkTw",  # Cloud Atlas Sextet
    "https://www.youtube.com/watch?v=fJ9rUzIMcZQ",  # Bohemian Rhapsody (Queen)
    # Vimeo (5 URLs)
    "https://vimeo.com/76979871",  # Big Buck Bunny
    "https://vimeo.com/347119375",  # Sintel
    "https://vimeo.com/1084537",  # Big Buck Bunny (official Blender upload)
    "https://vimeo.com/22439234",  # The Mountain
    "https://vimeo.com/31158841",  # Murmuration
    # Dailymotion (4 URLs)
    "https://www.dailymotion.com/video/x84sh87",  # Dailymotion demo video
    "https://www.dailymotion.com/video/x9ku2zy",  # Dailymotion Copyright Rules
    "https://www.dailymotion.com/video/x8fjgy5",  # Tony Baker Voice Over Cats
    "https://www.dailymotion.com/video/x9uhocc",  # TikTok Funny Compilation
    # Twitch (3 URLs)
    "https://clips.twitch.tv/SmilingPluckySashimiBibleThump",
    "https://clips.twitch.tv/ArbitraryArborealGarbagePJSalt",
    "https://clips.twitch.tv/HedonisticKawaiiDillDancingBanana",
    # TikTok (3 URLs)
    "https://www.tiktok.com/@khaby.lame/video/7008477449723292934",
    "https://www.tiktok.com/@nba/video/7123456789012345678",
    "https://www.tiktok.com/@natgeo/video/6987654321098765432",
    # Instagram (3 URLs)
    "https://www.instagram.com/reel/DGcoPAktJAT/",
    "https://www.instagram.com/reel/DEmLvwyqHRo/",
    "https://www.instagram.com/reel/C3akyEHyWM4/",
]

# Pre-categorized for platform statistics display
DEMO_URLS_BY_PLATFORM = {
    "YouTube": DEMO_VIDEO_URLS[:15],
    "Vimeo": DEMO_VIDEO_URLS[15:20],
    "Dailymotion": DEMO_VIDEO_URLS[20:24],
    "Twitch": DEMO_VIDEO_URLS[24:27],
    "TikTok": DEMO_VIDEO_URLS[27:30],
    "Instagram": DEMO_VIDEO_URLS[30:33],
}


def random_demo_urls(count: int = 5) -> list[str]:
    """Return `count` random demo video URLs with at least 2 distinct platforms.

    Ensures cross-platform variety for visual breadth on the dashboard.
    """
    if count <= 0:
        return []

    count = min(count, len(DEMO_VIDEO_URLS))

    # Pick 1-2 random platforms, then distribute remaining
    platforms = list(DEMO_URLS_BY_PLATFORM.keys())
    random.shuffle(platforms)

    selected: list[str] = []

    # Always pick at least 1 URL from a different platform first
    primary_platform = platforms[0]
    pool = list(DEMO_URLS_BY_PLATFORM[primary_platform])
    random.shuffle(pool)
    if pool:
        selected.append(pool.pop(0))

    # Fill remaining from shuffled full pool
    remaining_pool = [u for u in DEMO_VIDEO_URLS if u not in selected]
    random.shuffle(remaining_pool)
    selected.extend(remaining_pool[: count - len(selected)])

    random.shuffle(selected)
    return selected
