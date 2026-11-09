# WP7b bench session: installer follow-ups and the status icon

The checklist is §5 of the WP7b brief (Plane page "12 — WP7b brief"). Record
the results in a comment on **LIVESTACK-55**, then tag `v0.2.0`. How-to
details are in [docs/install.md](../install.md); decisions in
[ADR 0010](../adr/0010-installer-followups-and-tray.md).

Use the installer from the pull request's release-workflow run (artifact
`baja-studio-<version>`), or from the `v0.2.0-rc.*` tag.

| # | Step | Expected | Result |
| --- | --- | --- | --- |
| 1 | On a PC with v0.1.0 installed, run the new installer with the defaults | No "steps failed" dialog; *Waiting for Baja Studio to start…* for ~10 s; home page shows the new version; settings and queue as before | |
| 2 | Fresh install on a PC that never had Baja Studio (or after uninstall with *No* to keeping data) | No "steps failed" dialog; `logs\caddy-stderr.log` has one "serving initial configuration" | |
| 3 | Log off and on | The Baja car with a **green** dot (or amber if playout/audio are not set up: hover names them) by the clock | |
| 4 | Look at the icon at 100 % and at 125 %/150 % display scaling, light and dark taskbar | The car (cage and wheels) is legible; the dot is clearly green/amber/red; not mistaken for the worker's "B" | |
| 5 | Services app → stop *Baja Studio* | Dot turns **red** within 5 s; tooltip "o serviço não responde" | |
| 6 | Tray → *Reiniciar os serviços*, accept UAC | Both services running again; dot back to green/amber within ~15 s | |
| 7 | Tray → *Gerar pacote de diagnóstico* | A zip appears in Downloads and opens | |
| 8 | Tray → *Abrir a pasta de logs*, *Readiness*, *Abrir o Baja Studio*, *Fechar este ícone* | Each does what it says; closing the icon changes nothing else | |
| 9 | Make sure `where ffprobe` finds nothing (no ffmpeg on PATH); drop a clip in the playout folder | Duration and thumbnail appear; Readiness → playout tools says `bundled (C:\Program Files\BajaStudio\ffmpeg\…)` | |
| 10 | Settings → Gateway on the fresh install | Hostname empty, `live.bajasaebrasil.net` greyed out as a hint; Readiness → gateway DNS says *not set* | |
| 11 | Run the installer again and untick the status icon option; log off and on | No icon at logon; Start menu → *Baja Studio status icon* still starts it | |
