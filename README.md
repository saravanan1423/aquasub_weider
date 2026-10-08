# Serial Raw Data Monitor

A browser UI for reading raw serial frames from weighing indicators and other serial devices. It preserves the sample script's CR/LF framing and quiet-gap fallback, and shows text, hexadecimal, or decimal byte views.

Continuous streams that never send CR/LF are handled automatically using an
internal quiet-gap and maximum buffering timeout.

## Live-weight extraction

The live-weight panel extracts a value from the rolling raw byte stream. For a
stream such as `05[000000]$%05[000000]$%...`, use start character `$`, end
character `]`, start address `5`, and end address `10`. Addresses are one-based
and inclusive, so this configuration displays `000000`.

## SQLite storage

Data is stored in `instance/serial_monitor.db`. The `serial_settings` table keeps
the latest serial-port and parser configuration. Live parsed weights are shown
in the browser only and are not stored in the database.

The **Image Master** screen stores furnace uploads under `storage/master_images`;
each furnace folder has a `product_images` folder for its products. Existing
image URLs remain available for older capture records. Image metadata and
browser URLs are stored in the `product_images` SQLite table.

## USB report backup

In **Device Settings > USB Backup**, select a mounted USB drive, choose the same
date, shift, furnace, and optional time filters as the report generator, then
select **Back up CSV + PDF**. The app writes both files to a new folder under
`Weider Reports/` on the drive. Only administrators can use this action.

On Raspberry Pi OS, the drive must be mounted under `/media`, `/mnt`, or
`/run/media` and writable by the user running the app. Plugging in a drive does
not always mount it automatically on a headless Pi. Mount it first, then use
**Refresh drives**. USB detection requires the system `lsblk` command. Install
the updated Python dependencies with `pip install -r requirements.txt` before
running the app.

## Login and roles

The default administrator is `admin` with password `admin@123`. Passwords are
stored only as hashes. Administrators can use the **Users** screen to create
accounts and grant access independently to Main Screen, Communication Settings,
Product Master, and Capture Logs.

## Project structure

- `app.py` — Flask application bootstrap and blueprint registration
- `communication_setting/` — serial connection, frame reader, and settings API
- `main_screen/` — live weighment capture workflow
- `master_image/` — Product Master image storage and CRUD
- `logs/` — capture-log listing, pagination, and admin editing
- `admin/` — login, users, roles, and permission management
- `core/` — shared database initialization and authorization enforcement

The Communication Settings **Read timeout** controls the maximum number of
seconds that undelimited serial bytes are buffered before being shown. CR/LF
frames may complete earlier. The allowed range is 0.1 to 30 seconds.

The SQLite `audit_logs` table records field-level old and new values for
Communication Settings updates, Product Master edits/deletes, Capture Log
edits/deletes, user updates, and password changes. Audit data is database-only;
password values and hashes are never written to it.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The program opens <http://127.0.0.1:5000> in fullscreen automatically. Set
`WEIGHTFLOW_AUTO_FULLSCREEN=0` before starting it to disable automatic browser
launching. The page automatically selects the first detected
`/dev/ttyUSB*` or `/dev/ttyACM*` device. Confirm the settings and click **Connect**.

## Linux serial-port setup

List connected serial devices:

```bash
python3 -m serial.tools.list_ports -v
```

Most Debian/Ubuntu systems require your user to belong to the `dialout` group:

```bash
sudo usermod -aG dialout "$USER"
```

Log out and back in after running that command. Verify access with:

```bash
groups
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

Common Linux ports are `/dev/ttyUSB0` for USB-to-serial adapters and
`/dev/ttyACM0` for CDC ACM devices. Only one application can normally open a
serial port at a time, so close tools such as `minicom` before connecting here.
