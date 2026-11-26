// Voice control: speech recognition -> PanVoice.parseIntent (voice-intents.js) -> the same player
// controls the buttons and keyboard shortcuts use (top-level consts/functions from app.js, which
// loads first). Tap the mic to start listening and tap again to stop. The "pan..." toggle decides
// whether commands must start with the wake word (so chatter nearby doesn't trigger anything).
(function () {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const micBtn = document.getElementById("voice-btn");
  const handsFreeBtn = document.getElementById("voice-handsfree");
  const toastEl = document.getElementById("voice-toast");
  if (!SpeechRecognition || !micBtn) return; // unsupported browser: controls stay hidden

  const HANDSFREE_KEY = "pan.voiceHandsFree";
  const RESTART_DELAY_MS = 400;
  const TOAST_MS = 2200;

  let recognition = null;
  let handsFree = false; // true = commands must start with the wake word
  let wanted = false; // the mic is latched on until tapped again
  let listening = false; // a recognition session is actually running right now
  let stopRequested = false;
  let toastTimer = null;

  function showVoiceToast(text) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toastEl.hidden = true;
    }, TOAST_MS);
  }

  async function markTrack(field, label) {
    const track = currentIndex !== -1 ? queue[currentIndex] : null;
    if (!track) {
      showVoiceToast("Nothing playing");
      return;
    }
    // Sets (never toggles): "love this" twice should not un-love it. The server's favorite and
    // dislike are mutually exclusive, same as the buttons rely on.
    const res = await fetch(`/track/${track.id}/${field}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [field]: true }),
    });
    showVoiceToast(res.ok ? label : "Couldn't save that");
  }

  async function executeIntent(intent) {
    switch (intent.type) {
      case "play":
        if (queuePlayer.paused) playPauseBtn.click();
        showVoiceToast("Play");
        break;
      case "pause":
        if (!queuePlayer.paused) playPauseBtn.click();
        showVoiceToast("Paused");
        break;
      case "next":
        nextBtn.click();
        showVoiceToast("Next");
        break;
      case "prev":
        prevBtn.click();
        showVoiceToast("Previous");
        break;
      case "volume-delta":
        adjustVolumeBy(intent.delta);
        showVoiceToast(intent.delta > 0 ? "Louder" : "Quieter");
        break;
      case "volume-set":
        setQueueVolume(intent.percent);
        showVoiceToast(`Volume ${intent.percent}%`);
        break;
      case "mute":
        toggleMute();
        showVoiceToast(queuePlayer.muted ? "Muted" : "Unmuted");
        break;
      case "shuffle":
        shuffleBtn.click();
        showVoiceToast(shuffle ? "Shuffle on" : "Shuffle off");
        break;
      case "repeat":
        repeatBtn.click();
        showVoiceToast(`Repeat ${repeatMode}`);
        break;
      case "favorite":
        await markTrack("favorite", "Favorited");
        break;
      case "dislike":
        await markTrack("dislike", "Disliked");
        break;
      case "search": {
        showVoiceToast(`Searching: ${intent.query}`);
        const count = await runSearch(intent.query);
        if (count === 0) {
          showVoiceToast(`No results for "${intent.query}"`);
        } else {
          playAllBtn.click();
        }
        break;
      }
      default:
        break; // "ignore"
    }
  }

  function render() {
    micBtn.classList.toggle("is-listening", wanted);
    micBtn.setAttribute("aria-pressed", String(wanted));
    handsFreeBtn.classList.toggle("is-active", handsFree);
    handsFreeBtn.setAttribute("aria-pressed", String(handsFree));
  }

  function start() {
    recognition = new SpeechRecognition();
    recognition.lang = navigator.language || "en-GB";
    recognition.continuous = true;
    recognition.interimResults = false;
    stopRequested = false;

    recognition.onresult = (event) => {
      const last = event.results[event.results.length - 1];
      if (!last.isFinal) return;
      const heard = last[0].transcript;
      const intent = PanVoice.parseIntent(heard, { handsFree });
      console.log("[voice] heard:", JSON.stringify(heard), "->", intent);
      // Without this, an utterance dropped for lacking the wake word looks like the mic is dead -
      // and shows what the recogniser actually made of "pan" (it often hears "pen", "pam", ...).
      if (intent.type === "ignore" && heard.trim()) showVoiceToast(`Heard: "${heard.trim()}"`);
      executeIntent(intent).catch(() => showVoiceToast("Voice command failed"));
    };
    recognition.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        stopRequested = true;
        wanted = false;
        showVoiceToast("Microphone blocked");
      }
      // "no-speech"/"aborted" are routine; onend decides whether to carry on.
    };
    recognition.onend = () => {
      listening = false;
      render();
      // Chrome ends continuous sessions on its own after a stretch of silence (or a command); carry
      // on for as long as the mic is latched on.
      if (wanted && !stopRequested) setTimeout(() => wanted && !listening && start(), RESTART_DELAY_MS);
    };

    try {
      recognition.start();
      listening = true;
    } catch {
      listening = false; // start() throws if one is already running
    }
    render();
  }

  function stop() {
    wanted = false;
    stopRequested = true;
    if (recognition) recognition.stop();
  }

  micBtn.hidden = false;
  handsFreeBtn.hidden = false;

  micBtn.addEventListener("click", () => {
    if (wanted) {
      stop();
      showVoiceToast("Voice off");
    } else {
      wanted = true;
      start();
      showVoiceToast(handsFree ? 'Listening: say "pan next"' : "Listening");
    }
  });

  handsFreeBtn.addEventListener("click", () => {
    handsFree = !handsFree;
    try {
      localStorage.setItem(HANDSFREE_KEY, handsFree ? "1" : "0");
    } catch {
      // localStorage unavailable (private browsing etc) - preference just won't persist
    }
    showVoiceToast(handsFree ? 'Wake word on: start commands with "pan"' : "Wake word off: any speech is a command");
    render();
  });

  // Only the wake-word preference is remembered; the mic always starts off, since browsers want a
  // tap before it opens anyway.
  try {
    handsFree = localStorage.getItem(HANDSFREE_KEY) === "1";
  } catch {
    handsFree = false;
  }
  render();
})();
