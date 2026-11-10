/**
 * A soft two-note chime (WebAudio), for a new message: on the hosts screen
 * when production turned it on (WP9), on the Hosts page for a host's
 * message (WP9b, on by default: production is not on IEMs).
 */

export function playChime(): void {
  const AudioContextClass = window.AudioContext as typeof AudioContext | undefined;
  if (AudioContextClass === undefined) return;
  const context = new AudioContextClass();
  [660, 880].forEach((frequency, index) => {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const start = context.currentTime + index * 0.15;
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(0.08, start);
    gain.gain.exponentialRampToValueAtTime(0.001, start + 0.4);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start(start);
    oscillator.stop(start + 0.4);
  });
  setTimeout(() => void context.close(), 1000);
}
