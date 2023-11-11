from curl_cffi import requests

# Import the required modules
import os
import discord
from datetime import datetime, timedelta
import aiohttp
from yt_dlp import YoutubeDL
from urllib.parse import urlparse, parse_qs
import hashlib
import asyncio
import subprocess
import re

intents = discord.Intents.default()

# Create a discord client
client = discord.Client(intents=intents)

# Define the channel id where the clips will be posted
CHANNEL_WHITELIST = [
    id.strip() for id in os.getenv("CHANNEL_WHITELIST", "").split(",") if id
]
ROLE_WHITELIST = [role.strip() for role in os.getenv("ROLE_WHITELIST", "").split(",") if role]
VIDEO_CHANNEL_WHITELIST = [channel.strip() for channel in os.getenv("VIDEO_CHANNEL_WHITELIST", "").split(",") if channel]
TOKEN = os.getenv("TOKEN")
CLIPS_API_URL = os.getenv("API_URL")
APIKEY = os.getenv("APIKEY")
INTERVAL_MINUTES = int(os.getenv("INTERVAL_MINUTES", "15"))


def get_video_info(url):
    parsed = urlparse(url)
    netloc = parsed.netloc
    query = parsed.query
    path = parsed.path
    # platforms = {
    #     "youtube": ["youtube.com", "youtu.be"],
    #     "kick": ["kick.com"],
    #     "rumble": ["rumble.com"],
    # }
    if netloc == "youtube.com":
        video_platform = "youtube"
        video_id = parse_qs(query)["v"][0]
    elif netloc == "youtu.be":
        video_platform = "youtube"
        video_id = path.split("/")[-1]
    elif netloc == "kick.com":
        video_platform = "kick"
        video_id = path.split("/")[-1]
    elif netloc == "rumble.com":
        video_platform = "rumble"
        video_id = path.split("/")[-1].rstrip(".html")
    else:
        video_platform = ""
        video_id = ""

    return video_platform, video_id


def time_str_to_seconds(time_str):
    # Split the time string by colon
    parts = time_str.split(":")
    # Initialize the total seconds to zero
    total_seconds = 0
    # Loop over the reversed parts
    for i, x in enumerate(reversed(parts)):
        # Convert each part to integer and multiply by 60 raised to the power of its index
        total_seconds += int(x) * 60**i
    # Return the total seconds
    return total_seconds


def extract_tags(string):
    # initialize an empty list to store the tags
    tags = []
    # loop through the string from the end to the beginning
    i = len(string) - 1
    while i >= 0:
        # if we encounter a closing bracket, start a new tag
        if string[i] == "]":
            tag = ""
        # if we encounter an opening bracket, add the tag to the list and skip the bracket
        elif string[i] == "[":
            tags.append(tag.lower())
        # otherwise, add the character to the tag
        else:
            tag = string[i] + tag
        # move to the previous character
        i -= 1
    return tags


async def submit_clip_to_db(source_url, start, end, title, clip_url):
    platform, slug = get_video_info(source_url)
    tags = extract_tags(title)
    start_seconds = time_str_to_seconds(start)
    duration = time_str_to_seconds(end) - start_seconds
    tag_ids = []
    async with aiohttp.ClientSession() as session:
        response = await session.post(
            CLIPS_API_URL + "/clips",
            json={
                "slug": slug,
                "start": start_seconds,
                "duration": duration,
                "title": title,
                "url": clip_url,
                "platform": platform,
            },
            headers={
                "apikey": APIKEY,
                "Authorization": f"Bearer {APIKEY}",
                "Prefer": "return=representation",
            },
        )
        clips = await response.json(content_type=None)

        for tag in tags:
            response = await session.get(
                CLIPS_API_URL + f"/tags?name=eq.{tag}",
                headers={
                    "apikey": APIKEY,
                    "Authorization": f"Bearer {APIKEY}",
                },
            )
            res = await response.json()

            if len(res) == 0:
                response = await session.post(
                    CLIPS_API_URL + "/tags",
                    json={"name": tag},
                    headers={
                        "apikey": APIKEY,
                        "Prefer": "return=representation",
                        "Authorization": f"Bearer {APIKEY}",
                    },
                )
                res = await response.json()

            tag_ids.append(res[0]["id"])

            response = await session.post(
                CLIPS_API_URL + "/cliptags",
                json=[
                    {"clip_id": clips[0]["id"], "tag_id": tag_id} for tag_id in tag_ids
                ],
                headers={
                    "apikey": APIKEY,
                    "Authorization": f"Bearer {APIKEY}",
                },
            )


# Define a function to download a video from a url
async def download_video(url, start, end, output, max_filesize_mb=None):
    print(ffdl.ffmpeg_path)
    print(f"Downloading file {output}...")
    video = url
    if url.startswith("https://kick.com/video/"):
        response = requests.get(
            "https://kick.com/api/v1/video/" + url[23:], impersonate="chrome101"
        )
        res_json = response.json()
        video = res_json["source"]
        channel = "https://kick.com/" + res_json["livestream"]["channel"]["slug"]
        if not channel in VIDEO_CHANNEL_WHITELIST:
            print('Channel is not in whitelist')
            return False
    else:
        with YoutubeDL() as ydl:
            try:
                info = ydl.extract_info(video, download=False)
                if not info.get('channel_url') in VIDEO_CHANNEL_WHITELIST and not ('uploader_url' in info and info.get('uploader_url') in VIDEO_CHANNEL_WHITELIST):
                    print('Channel is not in whitelist')
                    return False
            except:
                print('Error extracting video info')
                return False

    args = [
        "yt-dlp",
        video,
        "--download-sections",
        f"*{start}-{end}",
        "--force-keyframes-at-cuts",
        "-o",
        output,
        "--merge-output-format",
        "mp4",
    ]

    if max_filesize_mb:
        output = "[lowres]_" + output
        args.append(f"-f best[filesize<{max_filesize_mb}M]")

    cmd = " ".join(args)
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await proc.communicate()

    print("Download command complete")
    if stdout:
        print(f"[stdout]\n{stdout.decode()}")
    if stderr:
        print(f"[stderr]\n{stderr.decode()}")
    return True


# Define an async function to upload a file to transfer.sh
async def upload_file_tsh(file):
    # Create an aiohttp session
    async with aiohttp.ClientSession() as session:
        # Add the file to the form data
        print(f"Uploading file {file}")
        # Send a post request to transfer.sh with the form data
        response = await session.put(
            "https://transfer.sh/" + file, data=open(file, "rb")
        )
        # Return the response url
        message = await response.text()
        return message


async def upload_file_cb(file_path):
    # create an aiohttp session
    async with aiohttp.ClientSession() as session:
        # open the file in binary mode
        # read the file content
        file_content = open(file_path, "rb")
        # create a multipart form data object
        data = aiohttp.FormData()
        # add the file content as a field named "reqtype"
        data.add_field("reqtype", "fileupload")
        # add the file content as a field named "fileToUpload"
        data.add_field("fileToUpload", file_content, filename=file_path)
        # post the data to the catbox.moe API endpoint
        resp = await session.post("https://catbox.moe/user/api.php", data=data)
        # check if the response status is OK
        if resp.status == 200:
            # read the response text
            text = await resp.text()
            # return the text as the uploaded file URL
            return text
        else:
            # raise an exception with the response status
            raise Exception(f"Upload failed with status {resp.status}")


# Define an async function to process a message that contains a clip command
async def process_message(message):
    # Split the message by spaces
    args = re.sub(r"\s+", " ", message.content).strip().split(maxsplit=4)
    # Check if the message has four arguments
    if len(args) == 5:
        # Extract the video url, start time, end time, and title
        video_url = args[1]
        start_time = args[2]
        end_time = args[3]
        title = args[4]
        reply_text = title
        if video_url == "latest":
            # Extract the video url, start time, end time, and title
            response = requests.get(
                "https://kick.com/api/v1/channels/infrared", impersonate="chrome101"
            )
            response_json_obj = response.json()
            video_url = (
                "https://kick.com/video/"
                + response_json_obj["previous_livestreams"][0]["video"]["uuid"]
            )
            reply_text = f"{title} <{video_url}>"

        file_hash = hashlib.md5(
            (start_time + "-" + end_time + video_url).encode()
        ).hexdigest()
        # Generate a file name for the clip
        file_name = f"{file_hash}.mp4"
        # Download the video and create the clip
        download_success = await download_video(video_url, start_time, end_time, file_name)

        if download_success:
            # Get the file size of the clip
            file_size = os.path.getsize(file_name)

            max_file_size = 25000000

            boosts = message.guild.premium_subscription_count

            if boosts >= 14:
                max_file_size = 100000000
            elif boosts >= 7:
                max_file_size = 50000000

            # Check if the file size is within the limit
            if file_size <= max_file_size:
                print("Uploading clip directly to discord...")
                # Upload the clip directly to discord
                clip = discord.File(file_name)
                # Send the message to the channel
                sentMessage = await message.reply(reply_text, file=clip)
                clip_url = sentMessage.attachments[0].url
            else:
                if file_size <= 200 * 1000 * 1000:
                    print("Uploading clip to catbox...")
                    clip_url = await upload_file_cb(file_name)
                else:
                    print("Uploading clip to transfer.sh...")
                    # Upload the clip to transfer.sh
                    clip_url = await upload_file_tsh(file_name)
                # Send the message to the channel
                await message.reply(reply_text + "\n" + clip_url)

            await submit_clip_to_db(video_url, start_time, end_time, title, clip_url)

    else:
        # Send an error message to the user
        await message.reply(
            "Invalid command. Please use <video_url> <start_time> <end_time> <title[tag1][tag2]>"
        )


# Define an event handler for when the bot is ready
@client.event
async def on_ready():
    # Print a message to the console
    print("Connected to Discord!")

    tasks = []
    for channel_str in CHANNEL_WHITELIST:
        channel_id = int(channel_str)
        # Get the channel where the clips will be posted
        channel = client.get_channel(channel_id)

        if channel:
            # Get the current time
            last_checked_time = datetime.now() - timedelta(minutes=INTERVAL_MINUTES)
            print("Searching messages...")
            # Get the messages that mention the bot in the last hour
            messages = [
                message async for message in channel.history(after=last_checked_time)
            ]
            print(f"{len(messages)} messages retrieved")

            messages_to_process = []
            for message in messages:
                if (
                    len(message.mentions) == 1
                    and message.mentions[0].id == client.user.id
                ):
                    message_author = message.guild.getMember(message.author.id)
                    for role in message_author.author.roles:
                        if role.name in ROLE_WHITELIST:
                            messages_to_process.append(message)
                            break

            print(f"Processing {len(messages_to_process)} messages...")
            # Process each message
            for message in messages_to_process:
                tasks.append(process_message(message))
    await asyncio.gather(*tasks)
    await client.close()


# Run the bot with the token
client.run(TOKEN)
