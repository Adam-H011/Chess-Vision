# ChessVision Local Import extension

This extension sends a board only when you press **Import current board**. It talks only to the temporary `127.0.0.1` address and random session token currently displayed by ChessVision; it does not read cookies, use Chess.com APIs, or observe network traffic.

Firefox: open `about:debugging#/runtime/this-firefox`, choose **Load Temporary Add-on**, and select `manifest.json` in this directory.

Chromium: open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select this directory.

In ChessVision choose Browser as the source, press **Start Browser Import**, then copy its address and token into the popup. Visit an allowed Chess.com analysis, completed-game, bot-training, or coaching view, then press **Import current board**. Page layouts are not yet verified against a live Chess.com page; inspect all imported positions and fill in missing metadata before analysis.
