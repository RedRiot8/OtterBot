import os
import json
import asyncio
import threading

import discord
from flask import Flask
from pathlib import Path
from dotenv import load_dotenv
from discord.ext import commands

load_dotenv()

# Intents (message content is needed to read answers)
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

SCORES_FILE = Path("scores.json")


def load_scores() -> dict:
    if not SCORES_FILE.exists():
        return {}
    try:
        with SCORES_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_scores(scores: dict) -> None:
    try:
        with SCORES_FILE.open("w", encoding="utf-8") as f:
            json.dump(scores, f, indent=2)
    except OSError:
        # In a simple bot we silently ignore write errors
        pass

# Define your scavenger hunt questions and answers
TRIVIA_QUESTIONS = [
    {
        "question": "Question 1: What is the capital of France?",
        "answer": "paris",
    },
    {
        "question": "Question 2: In computing, what does 'CPU' stand for?",
        "answer": "central processing unit",
    },
    {
        "question": "Question 3: Which planet is known as the Red Planet?",
        "answer": "mars",
    },
]


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")


@bot.command(name="startotter")
async def startotter(ctx: commands.Context):
    """Starts a 3-question trivia scavenger hunt."""
    await ctx.send(
        f"Welcome to the scavenger hunt, {ctx.author.mention}! "
        "Answer the questions correctly to proceed. Let's begin!"
    )

    def check(m: discord.Message):
        # Only accept messages from the command user in the same channel
        return m.author == ctx.author and m.channel == ctx.channel

    for idx, item in enumerate(TRIVIA_QUESTIONS, start=1):
        question = item["question"]
        correct_answer = item["answer"]

        await ctx.send(question)

        while True:
            try:
                # Wait for the user's next message as an answer
                msg: discord.Message = await bot.wait_for(
                    "message", timeout=60.0, check=check
                )
            except asyncio.TimeoutError:
                await ctx.send(
                    f"{ctx.author.mention}, time's up! Hunt canceled due to inactivity."
                )
                return

            user_answer = msg.content.strip().lower()

            if user_answer == correct_answer:
                # Award 10 points for a correct answer
                scores = load_scores()
                user_id = str(ctx.author.id)
                current = scores.get(user_id, {"name": ctx.author.name, "points": 0})
                current["name"] = ctx.author.name
                current["points"] = int(current.get("points", 0)) + 10
                scores[user_id] = current
                save_scores(scores)

                if idx < len(TRIVIA_QUESTIONS):
                    await ctx.send(
                        f"🎉 Correct, {ctx.author.mention}! Here's your next clue..."
                    )
                else:
                    await ctx.send(
                        f"🎉 Correct, {ctx.author.mention}! "
                        "You've completed the scavenger hunt! Great job!"
                    )
                break  # Move on to the next question
            else:
                await ctx.send(
                    f"❌ Not quite, {ctx.author.mention}. Try again!"
                )


@bot.command(name="topotter")
async def topotter(ctx: commands.Context):
    """Show the top 5 players by points."""
    scores = load_scores()
    if not scores:
        await ctx.send("No scores yet. Play the scavenger hunt with `!startotter`!")
        return

    # Sort by points descending
    sorted_players = sorted(
        scores.items(),
        key=lambda item: int(item[1].get("points", 0)),
        reverse=True,
    )[:5]

    embed = discord.Embed(
        title="Scavenger Hunt Leaderboard",
        color=discord.Color.gold(),
    )

    for rank, (user_id, info) in enumerate(sorted_players, start=1):
        name = info.get("name", f"User {user_id}")
        points = info.get("points", 0)
        embed.add_field(
            name=f"#{rank} {name}",
            value=f"Points: {points}",
            inline=False,
        )

    await ctx.send(embed=embed)


app = Flask(__name__)


@app.route("/")
def index():
    return "Bot is Alive!"


def run_web():
    app.run(host="0.0.0.0", port=8080)


if __name__ == "__main__":
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()

    bot.run(os.getenv("DISCORD_TOKEN"))
