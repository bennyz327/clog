<p align="center">
  <img src="static/app-splash.png" width="20%" alt="圖片說明">
</p>

# CreatorLog (`clog`)

[繁體中文 README](README.md)

`clog` is a local-first tool for organizing creator records, profile URLs, posts, reminders, and work logs in one place.

It is meant for people who want to keep their tracking data on their own computer. By default, data is stored locally in SQLite rather than in a cloud service.

## What It Does

- Create a main record for each creator you track
- Store profile URLs and platform accounts in one place
- Save post links and auto-fill some metadata on supported sites
- Set reminders for creators you want to revisit later
- Keep work logs, notes, and research history
- Search across creators, URLs, posts, and notes

## Quick Start

`clog` currently comes in two forms:

- `clog`: desktop GUI for daily use
- `clog-cli`: command-line tool for scripts, automation, or fast entry

For most users, the easiest way to start is to download a packaged release:

1. Open [Releases](https://github.com/bennyz327/clog/releases)
2. Download the archive for your platform: `windows-x64` or `linux-x64`
3. Extract it and launch `clog` (`clog.exe` on Windows)
4. On first launch, the app creates its local data files in the app directory

If you only want the GUI, you can ignore the CLI.

## Common Usage

### GUI

The GUI is the main daily workflow for most people:

1. Add a creator
2. Attach a profile URL or account page
3. Save posts you want to keep
4. Add reminders or work logs when needed
5. Use search to jump back to the same creator later

### CLI

The CLI is useful if you prefer the terminal or want to integrate `clog` into your own workflow:

```bash
clog-cli init
clog-cli add "Creator Name" https://x.com/example
clog-cli name "#1" "Old Alias"
clog-cli url "#1" https://www.pixiv.net/users/123456
clog-cli post https://x.com/example/status/123
clog-cli remind "#1" 7d --note "check updates"
clog-cli work "#1" "Commission status noted."
clog-cli search example
clog-cli show "#1"
```

Main commands:

| Purpose | Command |
|---|---|
| Initialize | `clog-cli init` |
| Add creator | `clog-cli add NAME [URL]` |
| Add name | `clog-cli name TARGET NAME [CONTEXT]` |
| Add creator URL | `clog-cli url TARGET URL` |
| Save post | `clog-cli post URL [TARGET]` |
| Set reminder | `clog-cli remind TARGET WHEN` |
| Show reminders | `clog-cli due` |
| Recent lists | `clog-cli ls` |
| Add work log | `clog-cli work TARGET CONTENT` |
| Search | `clog-cli search QUERY` |
| Show creator record | `clog-cli show TARGET` |

Short aliases still exist, but the public docs use long-form commands for readability.

## For Developers

To run from source:

```bash
python -m pip install -r requirements.txt
python clog_cli.py init
python clog_gui.py
```

Example CLI usage from source:

```bash
python clog_cli.py add "Creator Name" https://x.com/example
python clog_cli.py search example
```

Main dependencies:

- Python 3.13
- PySide6
- gallery-dl
- PyInstaller for packaging

## More Technical Details

- Data model and ER notes: [`docs/ER.md`](docs/ER.md)
- Release builds for Windows and Linux are produced by GitHub Actions

## License

This project is licensed under the [MIT License](LICENSE).
