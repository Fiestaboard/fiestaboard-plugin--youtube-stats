# YouTube Stats Setup Guide

Show any YouTube channel's subscribers, views and video count on your board using your own free YouTube Data API key.

## Overview

**What it does:**

- Shows a channel's subscriber count, total views and number of public videos
- Works for any public channel, yours or someone else's
- Checks every 30 minutes by default

**Prerequisites:**

- A Google account, to create a free YouTube Data API key in Google Cloud Console. No billing account is needed.
- The channel's handle (the `@name` in its address) or its channel ID (starts with `UC`)
- About 5 minutes, once

There is no sign-in. The key only reads public channel information.

## Quick Setup

### 1. Enable the Plugin

In the FiestaBoard web UI:

1. Go to **Integrations**
2. Find **YouTube Stats** and toggle it **On**
3. Click **Configure**

### 2. Create a YouTube Data API key

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and sign in.
2. Create a project (the project picker at the top, then **New project**), or pick one you already have.
3. Open **APIs & Services** > **Library**, search for **YouTube Data API v3**, open it and press **Enable**.
4. Open **APIs & Services** > **Credentials**, press **Create credentials** > **API key**, and copy the key.
5. Recommended: press **Edit API key**, and under **API restrictions** choose **Restrict key** and tick only **YouTube Data API v3**. Leave **Application restrictions** at **None**: FiestaBoard calls YouTube from your own network, not from a website.

Each key gets 10,000 free quota units a day. This plugin uses 1 unit per check, so the default 30-minute refresh uses 48.

### 3. Configure the plugin

1. Paste the key into **YouTube Data API Key**
2. In **Channel**, enter the channel's handle, such as `@GoogleDevelopers`, or its channel ID, such as `UC_x5XG1OV2P6uZZ5FSM9Ttw`. You can also paste the channel's address, such as `https://www.youtube.com/@GoogleDevelopers`.
3. Optional: change **Refresh Interval** (300 to 86400 seconds, default 1800)
4. Click **Save Changes**

### 4. Create a Board Template

Create a page from the plugin's demo, or add variables to your own page. Center-align the lines:

```jinja
{{youtube_stats.logo_tile}} {{youtube_stats.channel_name}}

{{youtube_stats.subscribers_line}}
{{youtube_stats.views_line}}
{{youtube_stats.videos_line}}

```

### 5. View on Board

Send the page to your board. The numbers appear after the first check, a few seconds after saving.

## Template Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{{youtube_stats.channel_name}}` | The channel's title | `MAPLE ROAD COOKING` |
| `{{youtube_stats.handle}}` | The channel's handle | `@MAPLEROADCOOKING` |
| `{{youtube_stats.logo_tile}}` | A red tile | `{63}` |
| `{{youtube_stats.subscribers}}` | Subscribers, short form, or `HIDDEN` | `12.4K` |
| `{{youtube_stats.subscribers_exact}}` | Subscribers with separators, or `HIDDEN` | `12,400` |
| `{{youtube_stats.subscribers_hidden}}` | `YES` or `NO` | `NO` |
| `{{youtube_stats.views}}` | Total views, short form | `1.23M` |
| `{{youtube_stats.views_exact}}` | Total views with separators | `1,234,567` |
| `{{youtube_stats.videos}}` | Public videos, short form | `245` |
| `{{youtube_stats.videos_exact}}` | Public videos with separators | `245` |
| `{{youtube_stats.subscribers_line}}` | `12.4K SUBSCRIBERS` | `12.4K SUBSCRIBERS` |
| `{{youtube_stats.views_line}}` | `1.23M VIEWS` | `1.23M VIEWS` |
| `{{youtube_stats.videos_line}}` | `245 VIDEOS` | `245 VIDEOS` |

YouTube itself rounds subscriber counts down to 3 significant figures (12,456 subscribers is reported as 12,400), so the exact figure is only as exact as YouTube allows.

## Configuration Reference

| Setting | Required | Default | Description |
|---------|----------|---------|-------------|
| **YouTube Data API Key** (`api_key`) | Yes | | Your key from Google Cloud Console, with YouTube Data API v3 enabled |
| **Channel** (`channel`) | Yes | | Handle (`@name`), channel ID (`UC...`) or channel address |
| **Refresh Interval** (`refresh_seconds`) | No | `1800` | Seconds between checks, 300 to 86400 |

This plugin has no environment variables; everything is set in the web UI.

## Troubleshooting

**"YouTube rejected the API key"**
- Check the key was pasted whole, with no spaces. Create a new key under **APIs & Services** > **Credentials** if needed.

**"Your Google Cloud project does not allow the YouTube Data API"**
- In the project the key belongs to, open **APIs & Services** > **Library** > **YouTube Data API v3** and press **Enable**. It can take a few minutes to take effect.

**"YouTube refused this request (403)"**
- The key's restrictions block the call. Edit the key: allow **YouTube Data API v3** under **API restrictions**, and set **Application restrictions** to **None**.

**"This API key has used its daily YouTube quota"**
- The key shares its 10,000 daily units with anything else using it. The last numbers stay on the board, and the quota resets at midnight Pacific time. Raise **Refresh Interval** or use a key of its own.

**"YouTube found no channel"**
- Check the handle on the channel's page (it starts with `@`), or use the channel ID instead. Handles are matched exactly as YouTube has them.

**Subscribers show HIDDEN**
- The channel owner has hidden the subscriber count; YouTube does not share it with anyone.

**The numbers don't change**
- YouTube updates public counts slowly, and subscriber counts only move in 3-significant-figure steps (a channel with 12,400 subscribers shows 12.4K until it reaches 12,500).
