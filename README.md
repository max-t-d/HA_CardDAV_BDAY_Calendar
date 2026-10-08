# CardDAV Birthday Calendar

Home Assistant custom integration that reads the birthdays of your CardDAV
contacts (iCloud, Radicale, Nextcloud, …) and exposes them as **one calendar
entity** with yearly all-day events. Contacts stay the single source of truth;
nothing has to be maintained twice.

Why this exists: iOS builds its "Birthdays" calendar locally from your
contacts, so it is never served via CalDAV. The birthdays only exist in the
contacts, which are reachable via CardDAV.

Inspired by [nicklutt/ha-carddav-birthdays](https://github.com/nicklutt/ha-carddav-birthdays),
which creates one sensor per contact. This is a separate implementation
that creates a single calendar instead.

## Features

- One `calendar.birthdays` entity; works with the calendar card, the
  `calendar.get_events` action and calendar triggers
- Addressbook discovery: enter the server root (e.g. `https://contacts.icloud.com`),
  a direct addressbook URL also works
- Event titles like `🎂 Erika Mustermann (35)`; no age if the contact has no birth year
- Handles Apple's "birthday without year" placeholder (year 1604 / `X-APPLE-OMIT-YEAR`),
  `--MM-DD`, `YYYYMMDD` and 29 February (shown on 1 March in non-leap years)
- Reauth flow if the password is revoked; German and English UI
- No extra Python dependencies; refresh every 6 hours

## Installation

**HACS:** add this repository as a custom repository (type *Integration*), install,
restart Home Assistant.

**Manual:** copy `custom_components/carddav_bday_calendar` into your
`config/custom_components/` folder and restart.

Then go to *Settings → Devices & services → Add integration → CardDAV Birthday Calendar*.

### iCloud

- Server URL: `https://contacts.icloud.com`
- Username: your Apple Account email
- Password: an **app-specific password** (create one at account.apple.com →
  Sign-In and Security → App-Specific Passwords). Your normal password does not work.

## Example: notification on the morning of a birthday

```yaml
automation:
  - alias: Birthday reminder
    triggers:
      - trigger: calendar
        event: start
        entity_id: calendar.birthdays
        offset: "9:00:00"   # 09:00, the events start at midnight
    actions:
      - action: notify.notify
        data:
          message: "{{ trigger.calendar_event.summary }}"
```

## Notes

- The entity state is `on` on a day with a birthday; its attributes show the
  next birthday.
- Only contacts with a parseable `BDAY` appear. If an addressbook is large,
  the first refresh may take a moment.
