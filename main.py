import argparse
import asyncio
import sys

from curiosity.minecraft.bot import BotConfig, MinecraftBot, run_bot
from curiosity.minecraft.bot_server import main as run_server


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Minecraft Bot - Control Minecraft through Python",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start the web-based bot controller server
  python main.py server --mc-host localhost --mc-port 25565

  # Run the bot directly (CLI mode)
  python main.py bot --host localhost --port 25565 --username MyBot

  # Connect to a Minecraft server running on a different host
  python main.py server --mc-host 192.168.1.100 --mc-port 25565
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    server_parser = subparsers.add_parser("server", help="Run the web-based bot controller")
    server_parser.add_argument("--host", default="0.0.0.0", help="Web server bind host")
    server_parser.add_argument("--port", type=int, default=8766, help="Web server port")
    server_parser.add_argument("--mc-host", default="localhost", help="Minecraft server host")
    server_parser.add_argument("--mc-port", type=int, default=25565, help="Minecraft server port")
    server_parser.add_argument("--username", default="PythonBot", help="Bot username")

    bot_parser = subparsers.add_parser("bot", help="Run the bot directly (CLI mode)")
    bot_parser.add_argument("--host", default="localhost", help="Minecraft server host")
    bot_parser.add_argument("--port", type=int, default=25565, help="Minecraft server port")
    bot_parser.add_argument("--username", default="PythonBot", help="Bot username")

    args = parser.parse_args()

    if args.command == "server":
        sys.argv = [
            "minecraft-bot-server",
            "--host", args.host,
            "--port", str(args.port),
            "--mc-host", args.mc_host,
            "--mc-port", str(args.mc_port),
            "--username", args.username,
        ]
        run_server()
    elif args.command == "bot":
        asyncio.run(run_bot(args.host, args.port, args.username))
    else:
        parser.print_help()
        print("\nQuick start:")
        print("  1. Start a Minecraft server: cd minecraft_server && java -Xmx1024M -Xms1024M -jar server.jar nogui")
        print("  2. Run the bot server: python main.py server")
        print("  3. Open http://localhost:8766 in your browser")


if __name__ == "__main__":
    main()
