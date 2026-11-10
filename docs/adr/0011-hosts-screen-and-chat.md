# ADR 0011 — WP9: hosts screen and production chat

Date: 2026-11-09. Work item: LIVESTACK-4. Brief: Plane page "13 — WP9
brief". Depends on WP5b's `playout.hosts_view` (ADR 0009, decision 20).

## Decisions taken beyond the brief

### Cards

1. **`Module.hosts_cards()` beside `readiness_checks()`.** A card is a
   `HostsCard` (`core/hosts_cards.py`): `id`, `title`, `priority`,
   `state_path`, `component`, plus `options` (JSON handed to the component
   as is), `placeholder` and `pinned`. The new `hosts` module collects the
   cards of every running module once a second (its `screens` worker) and
   publishes the resolved layout in `hosts.layout`, so a module switched
   off takes its cards away and a pushed card of that module is cleared.
   A module whose `hosts_cards()` raises is left out and logged once.
   Recorded as a convention.
2. **Layout = production's list, then the rest by priority.** The setting
   `layout: [{id, visible}]` orders and shows/hides the cards it names;
   declared cards it does not name follow by priority, visible unless they
   are placeholders. Choices for cards nobody declares are kept (the module
   may come back) but ignored.
3. **Placeholders are declared by the hosts module** (`standings`,
   `team_facts`, `schedule`, hidden by default). A real card with the same
   id from any module replaces the placeholder, so the Baja app integration
   is that module's `hosts_cards()` plus one entry in the frontend's
   `CARD_COMPONENTS`.
4. **Now playing and Next up are two cards** (both from
   `playout.hosts_view`). The brief lists one card but the bench pushes
   "next up" to the top; with one card the push would also move the
   countdown. Priorities: now playing 100, next up 90, clock 60, audio 50,
   placeholders 10.
5. **The audio card's talkback macros are worked out by audio** from its
   settings: macros whose *on* steps open a send to the camera operator's
   bus (restore levels on a point on that bus, or a non-zero aux send to
   it). That bus is a new audio setting, `talkback_bus` (bus1–bus6,
   default bus6 as in the 2026 patch), picked on **Audio setup →
   Channels** by the bus labels, because a desk re-patched at an event must
   not need a code change. The card is rebuilt from the settings every
   second, so a saved change reaches the screens without a restart. The
   seats' microphones come from a new seat setting, `audio_channel`
   (default 1–4).
6. **The clock card** reads `playout.export` for the stream-start marker;
   with playout off it shows the wall clock only.
7. **The messages card is pinned** (`pinned: "bottom"`): always visible,
   always under the grid, not offered in the layout editor.

### Chat

8. **Messages live in `hosts_messages`** (one table, reactions and
   delivery as JSON columns). The "session" lasts until production presses
   **Clear history** (`clear_messages`, logged), so a service restart in
   the middle of a show keeps the conversation. State carries the last 50.
9. **"Exported with the aired log"** is done by the playout page: Export
   calls `playout.export_aired` and then `hosts.export_messages`, and the
   JSON download carries `hosts_messages` when the hosts module is on.
   Playout does not import or call the hosts module.
10. **Message fields beyond the brief's model**: `expires_at` (computed),
    `reply_seat_id`, `shown: [{seat_id, ts}]` (delivery) and
    `dismissed_at`. Reactions are `{seat_id, reaction, ts}`; they
    accumulate, so a host may tap 👍 then "need 1 min". A typed reply
    replaces the previous one.
11. **Delivery** is the hosts screen saying `mark_shown {message_id,
    seat_ids}` once per message and seat it shows a message *to* (its own
    seats, or all of them for "all"); a colleague's small message is not a
    delivery.
12. **Fading** is computed by the pages, not the server: a message fades
    once answered (reaction or reply), dismissed, or past `expires_at`.
    The default expiry is a setting, `message_expires_s` (60 s; 0 = never);
    the composer does not offer a per-message expiry (the command does).
13. **`from` is `production:<client id>`** from the command's source
    (`web:ab12` → `production:ab12`; other sources are kept whole).
14. **Extra commands**: `set_seat {seat_id, color?, enabled?,
    audio_channel?}` and `set_texts {canned_messages?, quick_replies?,
    message_expires_s?, chime?}` save settings through the store like
    `set_seat_name` and `set_card_layout`, so every edit is in the command
    log. `register_screen` and `mark_shown` stay out of the log
    (`wants_logged`), unless refused.

### Hosts screen

15. **Online and stale use one heartbeat.** The screen sends
    `register_screen {seats}` every 2 s. Server side, a seat is online
    while a screen showing it was heard from within 6 s
    (`ScreenTracker`, told the time; the `screens` worker expires them).
    Screen side, the red border shows when the socket is not open **or**
    no `register_screen` was answered for 4 s, because a pulled cable can
    leave a WebSocket "open" for minutes. Worst case about 4.5 s, inside
    the brief's 5 s.
16. **`/hosts` is a top-level route** outside the app frame (no navigation
    bar); `/hosts/control` is a normal page with a **Hosts** link in the
    bar. `useStateTree()` was added to the state hooks so cards can read
    any module's path.
17. **Typed replies use a plain text field.** A tablet shows its own
    on-screen keyboard for it; no keyboard of our own was built.
18. **The chime** is two short WebAudio tones for a new message to the
    screen's seats, only when the `chime` setting is on (default off).

### Readiness

19. Two checks: `seats` (fail with no seat on, warn for a seat in use
    without a name) and `screens` (warn with no hosts screen, or a seat in
    use that no screen shows; "not checked" when no seat is on). Screens
    are a warning, never a failure: the show can go on without them.

## Left open

- **Card content for standings, team facts and schedule** (out of scope;
  Baja app endpoints, BajaOvrLay thread).
- **Per-seat colours vs. the audio page's channel colours** are separate
  settings; they start equal and can drift.
- The bench (`docs/bench/wp9.md`) has not been run.
