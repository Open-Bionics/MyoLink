# MyoLink

An open-source Python library for discovering, connecting to, and controlling Open Bionics MyoLink devices (MyoPods, Hero RGD & Hero PRO) via Bluetooth Low Energy (BLE).

## Overview

MyoLink provides a simple and efficient asynchronous API to interact with Open Bionics' BLE-enabled hardware. It allows developers and researchers to:

*   Scan for nearby MyoLink-compatible devices.
*   Establish connections with Hands (Hero RGD, Hero PRO) and MyoPods (EMG sensors).
*   Send control commands to the Hand (e.g., setting individual digit positions, executing grips).
*   Subscribe to data streams from the Hand.
*   Configure MyoPods and stream EMG data.

## Features

*   Asynchronous API built on `bleak` and `asyncio`.
*   Device discovery with filtering for Open Bionics Company ID (`0x0ABA`).
*   High-level abstractions for `Hand` and `MyoPod` devices.
*   Built-in command encoding for Hand control.
*   Support for data stream subscriptions.

## Installation

```bash
# Ensure you have Python 3.8+ installed
pip install -r requirements.txt
```

## Basic Usage

(Examples will be added here as development progresses)

See the `examples/` directory for detailed usage scripts.

## Project Status

This project is currently under development. See `plan.md` for the development roadmap.

## Contributing

(Contribution guidelines will be added later)

## License

(License details to be determined - likely MIT or Apache 2.0)
