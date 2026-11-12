// Real-time beat detection on an AnalyserNode. Onsets come from spectral flux
// weighted towards the bass; a running tempo estimate then keeps the onsets
// that fall on the beat and drops the notes in between. Shared by the content
// script and the tab-capture page.
class BeatDetector {
  static WINDOW = 1.5; // seconds of history behind the onset threshold
  static SENSITIVITY = 2.0; // standard deviations above the recent average
  static FLOOR = 0.006;
  static MIN_GAP = 0.27; // seconds between beats, about 220 BPM
  static BASS_WEIGHT = 2;
  static RATE = 50; // onset envelope samples per second, for tempo estimation
  static SPAN = 6; // seconds of envelope kept

  constructor(analyser) {
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = 0;
    analyser.minDecibels = -75;
    analyser.maxDecibels = -12;
    const hzPerBin = analyser.context.sampleRate / analyser.fftSize;
    this.analyser = analyser;
    this.bassEnd = Math.max(3, Math.round(170 / hzPerBin));
    // Stop below the hi-hats so they do not count as beats.
    this.end = Math.min(analyser.frequencyBinCount, Math.round(4000 / hzPerBin));
    this.bins = new Uint8Array(this.end);
    this.prev = new Float32Array(this.end);
    this.history = [];
    this.envelope = new Float32Array(BeatDetector.RATE * BeatDetector.SPAN);
    this.slot = -1;
    this.period = 0; // seconds per beat, 0 while unknown
    this.periodAt = 0;
    this.rising = false;
    this.candidate = null; // the previous tick, which may turn out to be a peak
    this.beats = [];
    this.lastPeak = 0;
    this.lastSound = -Infinity;
    this.bpm = 0;
  }

  // `t` is any steadily increasing clock in seconds. Call about 60 times a second.
  tick(t) {
    this.analyser.getByteFrequencyData(this.bins);
    let bass = 0;
    let rest = 0;
    let level = 0;
    for (let i = 1; i < this.end; i++) {
      const v = (this.bins[i] / 255) ** 2;
      const rise = v - this.prev[i];
      if (rise > 0) {
        if (i < this.bassEnd) bass += rise;
        else rest += rise;
      }
      this.prev[i] = v;
      level += v;
    }
    const onset = (BeatDetector.BASS_WEIGHT * bass) / (this.bassEnd - 1) + rest / (this.end - this.bassEnd);
    if (level / this.end > 0.002) this.lastSound = t;

    const history = this.history;
    while (history.length && t - history[0].t > BeatDetector.WINDOW) history.shift();
    let mean = 0;
    let squares = 0;
    for (const sample of history) {
      mean += sample.v;
      squares += sample.v * sample.v;
    }
    const count = history.length;
    mean /= count || 1;
    const deviation = Math.sqrt(Math.max(0, squares / (count || 1) - mean * mean));
    history.push({ t, v: onset });
    this.record(t, onset);

    // Judge the previous tick once this one shows it was the top of a rise.
    const result = { beat: false, strength: 0, bpm: this.bpm, signal: t - this.lastSound < 1.5 };
    const peak = this.candidate;
    if (peak && this.rising && onset <= peak.v && peak.loud && this.accept(peak)) {
      this.beats.push(peak.t);
      while (this.beats.length > 17 || peak.t - this.beats[0] > 12) this.beats.shift();
      this.lastPeak = peak.v;
      this.bpm = this.tempo();
      result.beat = true;
      result.strength = Math.min(1, Math.max(0.35, peak.excess));
      result.bpm = this.bpm;
    }
    this.rising = !peak || onset > peak.v;
    this.candidate = {
      t,
      v: onset,
      loud: count >= 20 && onset > BeatDetector.FLOOR && onset > mean + BeatDetector.SENSITIVITY * deviation,
      excess: (onset - mean) / (6 * deviation + 1e-6),
    };
    return result;
  }

  // Once the tempo is known, an onset soon after a beat only counts if it is
  // clearly the stronger of the two, which moves the dance onto the main beat.
  accept(peak) {
    const gap = peak.t - (this.beats.length ? this.beats[this.beats.length - 1] : -Infinity);
    if (gap < BeatDetector.MIN_GAP) return false;
    return gap >= 0.7 * this.period || peak.v > 1.35 * this.lastPeak;
  }

  record(t, onset) {
    const { RATE } = BeatDetector;
    const size = this.envelope.length;
    const slot = Math.floor(t * RATE);
    if (slot - this.slot > size) this.envelope.fill(0);
    else for (let s = this.slot + 1; s <= slot; s++) this.envelope[s % size] = 0;
    this.envelope[slot % size] = Math.max(this.envelope[slot % size], onset);
    this.slot = slot;
    if (t - this.periodAt >= 1) {
      this.periodAt = t;
      this.period = this.findPeriod();
    }
  }

  // Autocorrelation of the onset envelope over 60 to 180 BPM, leaning towards 120.
  findPeriod() {
    const { RATE } = BeatDetector;
    const size = this.envelope.length;
    const x = new Float32Array(size);
    let mean = 0;
    for (let i = 0; i < size; i++) {
      x[i] = this.envelope[(this.slot + 1 + i) % size];
      mean += x[i];
    }
    mean /= size;
    let power = 0;
    for (let i = 0; i < size; i++) {
      x[i] -= mean;
      power += x[i] * x[i];
    }
    if (power < 1e-9) return 0;
    let best = 0;
    let bestScore = 0;
    for (let lag = Math.round(RATE / 3); lag <= RATE; lag++) {
      let sum = 0;
      for (let i = lag; i < size; i++) sum += x[i] * x[i - lag];
      const correlation = sum / power;
      const octaves = Math.log2(lag / RATE / 0.5);
      const score = correlation * Math.exp(-0.5 * (octaves / 0.9) ** 2);
      if (correlation > 0.12 && score > bestScore) {
        bestScore = score;
        best = lag;
      }
    }
    return best / RATE;
  }

  // Median gap between recent beats, folded into a danceable range.
  tempo() {
    if (this.beats.length < 5) return 0;
    const gaps = [];
    for (let i = 1; i < this.beats.length; i++) gaps.push(this.beats[i] - this.beats[i - 1]);
    gaps.sort((a, b) => a - b);
    let bpm = 60 / gaps[gaps.length >> 1];
    while (bpm < 70) bpm *= 2;
    while (bpm >= 180) bpm /= 2;
    return Math.round(bpm);
  }
}
