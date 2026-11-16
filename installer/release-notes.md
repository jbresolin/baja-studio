## Install or upgrade

Download `BajaStudio-Setup-{version}.exe` below and run it on the streaming PC
(how: [docs/install.md](https://github.com/jbresolin/baja-studio/blob/main/docs/install.md)).
Windows SmartScreen asks once: *More info* → *Run anyway*.

**Upgrade from v0.1.0: nothing to do.** Run the new installer over the old one:
settings, the playout queue, logs and certificates stay where they are. ffmpeg
and ffprobe now come with the installer, so a copy installed by hand is no
longer needed (a full path set in Playout → Settings → Tools still wins).
The status icon appears in the taskbar at the next logon.

After installing, open **Readiness** and run all checks.

**New: the camera node (trackside laptops).** `BajaCameraNode-{version}.exe`,
also below, installs on a laptop with one or two 4K webcams and offers them to
the studio as crop cameras (a digital pan, tilt and zoom), with no OBS needed
on the laptop, or with the laptop's OBS doing the video if you prefer. It runs
as the *Baja camera node* service, opens TCP 8891 (private and domain
networks) and has its own page at `http://<laptop>:8891/`: paste each camera's
SRT URL from the studio's **Feeds** page there, then add the laptop on the
studio's **Agents** page (port 8891). Its cameras' presets are kept by the
studio, and their previews show the whole picture with the window on it.

**New: Feeds (drone and trackside laptops).** The installer now also runs
MediaMTX as a third service, *Baja Studio feeds*, and (with the firewall
option ticked) opens TCP 1935 for RTMP and UDP 8890 for SRT on private and
domain networks. Add the drone and the laptops on the new **Feeds** page: it
shows the URL to type into each sender, whether each feed is live, and adds a
feed to OBS with one click. Upgrading stops the feeds service for the minute
the upgrade takes; restarting the studio alone never drops a feed.
