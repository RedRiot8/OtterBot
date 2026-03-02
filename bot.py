import os
import asyncio
import discord
from dotenv import load_dotenv
from discord.ext import commands

load_dotenv()

# Intents (message content is needed to read answers)
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

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


@bot.command(name="start_hunt")
async def start_hunt(ctx: commands.Context):
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


if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_TOKEN"))
