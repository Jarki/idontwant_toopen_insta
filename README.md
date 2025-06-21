# Instagram Reel Downloader

This is a Telegram bot that downloads user-restricted Instagram reels.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/Jarki/idontwant_toopen_insta.git
cd idontwant_toopen_insta
```

### 2. Copy .env.example to .env and fill in the required values using your preferred text editor

Note: Optional values can be skipped

```bash
cp .env.example .env
```

### 3. (Optional) Create a cookies.txt file

If you want to download user-restricted reels, you need to create a cookies.txt file.
Refer to https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp

Create a file named `cookies.txt` and put it into the `assets` directory.

### 4. Run the bot using Docker Compose

```bash
docker-compose up -d --build
```

## Usage

Send an instagram reel link to the bot and it will download the reel.