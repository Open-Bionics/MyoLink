# Project Plan: MyoLink Python Library

## 1. Goals

*   Create a Python library (`myolink`) for discovering, connecting to, and interacting with Open Bionics MyoLink devices (MyoPods, Hero RGD, Hero PRO) via BLE.
*   Provide clear and easy-to-use examples demonstrating the library's capabilities.
*   Ensure the library is efficient, robust, and follows Python best practices.

## 2. Architecture

*   **Core BLE Interaction:** Use the `bleak` library for cross-platform asynchronous BLE communication.
*   **Device Abstraction:**
    *   `myolink.core`: Base classes and functions for device discovery (filtering by Company ID `0x0ABA`) and connection management.
    *   `myolink.device.Hand`: Class representing a Hero RGD/PRO Hand, handling specific services, characteristics, commands (digit/grip control), and data subscriptions.
    *   `myolink.device.MyoPod`: Class representing a MyoPod, handling configuration and EMG data streaming.
*   **Command Encoding/Decoding:** Implement logic to pack and unpack data according to the MyoLink communication protocol (e.g., using the `struct` module for Hand commands).
*   **Asynchronous API:** Design the library with an `async`/`await` interface suitable for BLE operations.
*   **Examples:** Standalone scripts in the `examples/` directory demonstrating specific use cases.
*   **Documentation:** Basic usage documentation and API reference in the `docs/` directory.
*   **Testing:** Unit tests in the `tests/` directory to ensure core functionality correctness.

## 3. Development Roadmap

### Phase 1: Foundation & Hand Control

1.  [x] Set up project structure (`myolink/`, `examples/`, `docs/`, `tests/`).
2.  [x] Create `requirements.txt` (add `bleak`).
3.  [x] Create `README.md` (initial version).
4.  [x] Create `plan.md` (this file).
5.  [ ] Implement initial `myolink.core` module:
    *   Define constants (Company ID, Service/Characteristic UUIDs).
    *   Implement async device discovery function (`discover_devices`).
6.  [ ] Implement initial `myolink.device.Hand` module:
    *   Basic connection/disconnection logic.
    *   Implement `set_digit_positions` command encoding.
    *   Implement function to send `set_digit_positions` command.
7.  [ ] Create `examples/discover_devices.py`.
8.  [ ] Create `examples/connect_hand.py`.
9.  [ ] Create `examples/control_hand_digits.py`.
10. [ ] Add basic unit tests for discovery and command encoding.

### Phase 2: MyoPod & Data Streaming

1.  [ ] Implement initial `myolink.device.MyoPod` module:
    *   Basic connection/disconnection logic.
    *   Implement configuration methods (if known).
    *   Implement data streaming subscription and handling.
2.  [ ] Create `examples/connect_myopod.py`.
3.  [ ] Create `examples/stream_myopod_data.py` (simple console output first).
4.  [ ] Enhance Hand class with data stream subscription.
5.  [ ] Implement Grip Control command for Hand.
6.  [ ] Create `examples/control_hand_grip.py`.

### Phase 3: Advanced Features & Refinement

1.  [ ] Implement handling for multiple MyoPod connections.
2.  [ ] Create `examples/connect_multiple_myopods.py`.
3.  [ ] Create graphing examples (using `matplotlib` or `pyqtgraph`):
    *   `examples/graph_single_myopod.py`
    *   `examples/graph_multiple_myopods.py`
4.  [ ] Implement pairing logic (if required).
5.  [ ] Add comprehensive error handling and logging.
6.  [ ] Write more detailed documentation (`docs/`).
7.  [ ] Expand unit test coverage (`tests/`).
8.  [ ] Refactor and optimise codebase.

## 4. Key Technologies

*   Python 3.8+ (due to `bleak` requirements and async features)
*   `bleak` (BLE library)
*   `asyncio` (Python async framework)
*   `struct` (for data packing/unpacking)
*   `matplotlib` / `pyqtgraph` (for graphing examples)
*   `pytest` (for testing)

## 5. Notes

*   Refer to `OB2 Control Commands.pdf` for detailed command structures.
*   Ensure consistent use of tabs and British spelling conventions. 
*   

## 6. Future Ideas

*   Provide a library that uses hand tracking machine vision to control the hand.
