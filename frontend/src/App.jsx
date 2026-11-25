import React,{ useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AudioLines,
  CalendarDays,
  Check,
  CircleHelp,
  Clock3,
  HeartPulse,
  Mic,
  MicOff,
  Phone,
  Plus,
  Radio,
  ShieldCheck,
  Stethoscope,
  UsersRound,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import { api, apiUrl } from "./api.js";

const emptyDashboard = { doctors: [], appointments: [] };
const formatDate = (value) =>
  new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric" }).format(new Date(value));
const formatTime = (value) =>
  new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" }).format(new Date(value));
const displayPhone = (value) => {
  const digits = value.replace(/\D/g, "");
  return digits.length >= 4 ? `••• ••• ${digits.slice(-4)}` : "Hidden number";
};
const initials = (name) =>
  name
    .replace(/^Dr\.\s*/i, "")
    .split(" ")
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();

function App() {
  const [dashboard, setDashboard] = useState(emptyDashboard);
  const [isLoading, setIsLoading] = useState(true);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showDoctorForm, setShowDoctorForm] = useState(false);
  const [showBookingForm, setShowBookingForm] = useState(false);
  const [openSlotForm, setOpenSlotForm] = useState("");
  const [callState, setCallState] = useState("ended");
  const [callVisible, setCallVisible] = useState(false);
  const [callerName, setCallerName] = useState("");
  const [nameDraft, setNameDraft] = useState("");
  const [muted, setMuted] = useState(false);
  const [speakerEnabled, setSpeakerEnabled] = useState(true);
  const [callError, setCallError] = useState("");
  const [visualizerWarning, setVisualizerWarning] = useState("");
  const [audioPlaybackBlocked, setAudioPlaybackBlocked] = useState(false);
  const [callNotice, setCallNotice] = useState(null);
  const [transcript, setTranscript] = useState([]);
  const [highlightedAppointmentId, setHighlightedAppointmentId] = useState("");
  const [appointmentFilter, setAppointmentFilter] = useState("upcoming");
  const [callSeconds, setCallSeconds] = useState(0);
  const [notice, setNotice] = useState("");
  const peerRef = useRef(null);
  const streamRef = useRef(null);
  const remoteAudioRef = useRef(null);
  const socketRef = useRef(null);
  const transcriptEndRef = useRef(null);
  const callAttemptRef = useRef(0);
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const visualizerFrameRef = useRef(0);
  const visualizerRef = useRef(null);
  const highlightTimerRef = useRef(null);

  const refresh = useCallback(async () => {
    const [data, serviceStatus] = await Promise.all([
      api("/api/dashboard"),
      api("/api/health"),
    ]);
    setDashboard(data);
    setHealth(serviceStatus);
    setIsLoading(false);
    setError("");
  }, []);

  useEffect(() => {
    let active = true;
    let timer;
    const poll = async () => {
      try {
        await refresh();
      } catch (problem) {
        if (active) {
          setIsLoading(false);
          setError(problem.message);
        }
      } finally {
        if (active) timer = window.setTimeout(poll, 15000);
      }
    };
    void poll();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [refresh]);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript]);

  useEffect(() => {
    if (callState !== "connected") return undefined;
    const timer = window.setInterval(() => setCallSeconds((seconds) => seconds + 1), 1000);
    return () => window.clearInterval(timer);
  }, [callState]);

  useEffect(() => () => {
    window.clearTimeout(highlightTimerRef.current);
    window.cancelAnimationFrame(visualizerFrameRef.current);
    if (audioContextRef.current && audioContextRef.current.state !== "closed") {
      void audioContextRef.current.close().catch((problem) => {
        console.warn("Could not close the microphone waveform monitor.", problem);
      });
    }
  }, []);

  const upcomingAppointments = useMemo(
    () => dashboard.appointments.filter(
      (appointment) =>
        appointment.status === "booked" && new Date(appointment.starts_at).getTime() >= Date.now(),
    ),
    [dashboard.appointments],
  );
  const cancelledAppointments = useMemo(
    () => dashboard.appointments.filter((appointment) => appointment.status === "cancelled"),
    [dashboard.appointments],
  );
  const filteredAppointments = useMemo(() => {
    if (appointmentFilter === "all") return dashboard.appointments;
    if (appointmentFilter === "cancelled") return cancelledAppointments;
    return upcomingAppointments;
  }, [appointmentFilter, cancelledAppointments, dashboard.appointments, upcomingAppointments]);
  const availableCount = useMemo(
    () => dashboard.doctors.reduce((total, doctor) => total + doctor.available_slots.length, 0),
    [dashboard.doctors],
  );
  const nextSlot = dashboard.doctors
    .flatMap((doctor) => doctor.available_slots)
    .sort((left, right) => left.starts_at.localeCompare(right.starts_at))[0];

  function highlightAppointment(appointmentId) {
    if (!appointmentId) return;
    setHighlightedAppointmentId(appointmentId);
    window.clearTimeout(highlightTimerRef.current);
    highlightTimerRef.current = window.setTimeout(() => {
      setHighlightedAppointmentId((current) => current === appointmentId ? "" : current);
    }, 3500);
  }

  async function submitForm(event, path, body, done) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api(path, { method: "POST", body: JSON.stringify(body) });
      await refresh();
      if (path === "/api/appointments") highlightAppointment(result?.id);
      done();
      setNotice("Changes saved to the clinic schedule.");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (problem) {
      setError(problem.message);
    } finally {
      setBusy(false);
    }
  }

  async function cancelAppointment(appointment) {
    setError("");
    try {
      await api("/api/appointments/cancel", {
        method: "POST",
        body: JSON.stringify({
          phone_number: appointment.phone_number,
          appointment_id: appointment.id,
        }),
      });
      await refresh();
      setAppointmentFilter("all");
      highlightAppointment(appointment.id);
      setNotice("Appointment cancelled. Its time is available again.");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (problem) {
      setError(problem.message);
    }
  }

  function stopAudioVisualizer() {
    window.cancelAnimationFrame(visualizerFrameRef.current);
    visualizerFrameRef.current = 0;
    analyserRef.current = null;
    const audioContext = audioContextRef.current;
    audioContextRef.current = null;
    if (audioContext && audioContext.state !== "closed") {
      void audioContext.close().catch((problem) => {
        console.warn("Could not close the microphone waveform monitor.", problem);
      });
    }
  }

  async function startAudioVisualizer(stream) {
    const audioContext = new window.AudioContext();
    audioContextRef.current = audioContext;
    await audioContext.resume();
    const source = audioContext.createMediaStreamSource(stream);
    const analyser = audioContext.createAnalyser();
    const silentGain = audioContext.createGain();
    analyser.fftSize = 64;
    analyser.smoothingTimeConstant = 0.75;
    silentGain.gain.value = 0;
    source.connect(analyser);
    analyser.connect(silentGain);
    silentGain.connect(audioContext.destination);
    analyserRef.current = analyser;

    const levels = new Uint8Array(analyser.frequencyBinCount);
    let lastDraw = 0;
    const draw = (timestamp) => {
      if (!analyserRef.current) return;
      if (timestamp - lastDraw >= 50) {
        analyser.getByteFrequencyData(levels);
        const bars = visualizerRef.current?.children;
        if (bars) {
          for (let index = 0; index < bars.length; index += 1) {
            const bin = Math.floor(index * levels.length / bars.length);
            const level = Math.max(0.08, levels[bin] / 255);
            bars[index].style.setProperty("--voice-level", level.toString());
          }
        }
        lastDraw = timestamp;
      }
      visualizerFrameRef.current = window.requestAnimationFrame(draw);
    };
    visualizerFrameRef.current = window.requestAnimationFrame(draw);
  }

  function closeCallSummary() {
    setCallVisible(false);
    setCallState("ended");
    setCallerName("");
    setNameDraft("");
    setCallNotice(null);
  }

  function cancelCallSetup() {
    setCallVisible(false);
    setCallState("ended");
    setCallerName("");
    setNameDraft("");
    setCallNotice(null);
  }

  async function endCall() {
    callAttemptRef.current += 1;
    socketRef.current?.close();
    socketRef.current = null;
    peerRef.current?.close();
    peerRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (remoteAudioRef.current) {
      remoteAudioRef.current.pause();
      remoteAudioRef.current.srcObject = null;
      remoteAudioRef.current = null;
    }
    stopAudioVisualizer();
    setMuted(false);
    setSpeakerEnabled(true);
    setAudioPlaybackBlocked(false);
    setCallState("ended");
    await refresh().catch((problem) => setError(problem.message));
  }

  function openCallSetup() {
    setCallVisible(true);
    setCallError("");
    setVisualizerWarning("");
    setCallNotice(null);
    setCallerName("");
    setNameDraft("");
    setCallState("setup");
  }

  async function startCall() {
    const attempt = callAttemptRef.current + 1;
    callAttemptRef.current = attempt;
    setCallError("");
    setVisualizerWarning("");
    setSpeakerEnabled(true);
    setAudioPlaybackBlocked(false);
    setCallNotice(null);
    setTranscript([]);
    setCallSeconds(0);
    setCallState("ringing");
    try {
      const localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (callAttemptRef.current !== attempt) {
        localStream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = localStream;
      try {
        await startAudioVisualizer(localStream);
      } catch (problem) {
        stopAudioVisualizer();
        setVisualizerWarning("The live waveform is unavailable; call audio can still continue.");
        console.warn("Could not start the microphone waveform monitor.", problem);
      }
      if (callAttemptRef.current !== attempt) return;
      setCallState("connecting");
      const peer = new RTCPeerConnection({
        iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
      });
      peerRef.current = peer;
      peer.addEventListener("connectionstatechange", () => {
        if (peerRef.current !== peer) return;
        if (peer.connectionState === "connected") {
          setCallState("connected");
          setCallError("");
        } else if (peer.connectionState === "disconnected") {
          setCallState("reconnecting");
        } else if (peer.connectionState === "failed") {
          setCallState("failed");
          setCallError("The audio connection was interrupted. End the call and try again.");
        }
      });
      peer.addTrack(localStream.getAudioTracks()[0], localStream);
      peer.addEventListener("track", (event) => {
        const audio = new Audio();
        audio.autoplay = true;
        audio.playsInline = true;
        audio.srcObject = event.streams[0] ?? new MediaStream([event.track]);
        remoteAudioRef.current = audio;
        audio.play()
          .then(() => setAudioPlaybackBlocked(false))
          .catch(() => setAudioPlaybackBlocked(true));
      });
      const offer = await peer.createOffer();
      if (callAttemptRef.current !== attempt) return;
      await peer.setLocalDescription(offer);
      if (callAttemptRef.current !== attempt) return;
      if (peer.iceGatheringState !== "complete") {
        await new Promise((resolve, reject) => {
          const timeout = window.setTimeout(() => reject(new Error("Timed out connecting the audio session.")), 12000);
          peer.addEventListener("icegatheringstatechange", () => {
            if (peer.iceGatheringState === "complete") {
              window.clearTimeout(timeout);
              resolve();
            }
          });
        });
      }
      if (callAttemptRef.current !== attempt) return;
      const answer = await api("/api/offer", {
        method: "POST",
        body: JSON.stringify({ sdp: peer.localDescription.sdp, type: peer.localDescription.type }),
      });
      if (callAttemptRef.current !== attempt) return;
      await peer.setRemoteDescription({ sdp: answer.sdp, type: answer.type });
      if (callAttemptRef.current !== attempt) return;
      const wsUrl = new URL(`/api/transcript/${encodeURIComponent(answer.pc_id)}`, apiUrl());
      wsUrl.protocol = wsUrl.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(wsUrl);
      socketRef.current = socket;
      socket.addEventListener("message", (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.event?.type === "appointment_booked" || message.event?.type === "appointment_cancelled") {
            setCallNotice(message.event);
            setAppointmentFilter("all");
            highlightAppointment(message.event.appointment_id);
            void refresh().catch((problem) => setCallError(problem.message));
            return;
          }
          setTranscript((entries) => [...entries, { ...message, at: new Date() }]);
        } catch {
          setCallError("A transcript update could not be read.");
        }
      });
      socket.addEventListener("error", () => setCallError("The live transcript connection was interrupted."));
      socket.addEventListener("close", () => {
        if (socketRef.current === socket) {
          setCallError("The live transcript connection ended.");
        }
      });
    } catch (problem) {
      if (callAttemptRef.current !== attempt) return;
      socketRef.current?.close();
      socketRef.current = null;
      peerRef.current?.close();
      peerRef.current = null;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      stopAudioVisualizer();
      if (remoteAudioRef.current) {
        remoteAudioRef.current.pause();
        remoteAudioRef.current.srcObject = null;
        remoteAudioRef.current = null;
      }
      setMuted(false);
      setSpeakerEnabled(true);
      setAudioPlaybackBlocked(false);
      setCallState("failed");
      setCallError(problem.message);
    }
  }

  function toggleMute() {
    const nextMuted = !muted;
    streamRef.current?.getAudioTracks().forEach((track) => {
      track.enabled = !nextMuted;
    });
    setMuted(nextMuted);
  }

  function toggleSpeaker() {
    const nextEnabled = !speakerEnabled;
    if (remoteAudioRef.current) remoteAudioRef.current.muted = !nextEnabled;
    setSpeakerEnabled(nextEnabled);
  }

  const callOpen = callVisible;
  const callStatus = {
    setup: "CALL SETUP",
    ringing: "RINGING",
    connected: "LIVE · SECURE LINE",
    connecting: "CONNECTING",
    reconnecting: "RECONNECTING",
    failed: "CALL NOT CONNECTED",
    ended: "CALL ENDED",
  }[callState];
  const callSubtitle = {
    setup: "Optional display name · identity is verified by phone",
    ringing: "Requesting microphone access…",
    connected: "Your care assistant is listening",
    connecting: "Preparing your private call…",
    reconnecting: "Restoring your audio connection…",
    failed: "Close this window and try again when ready",
    ended: "Your conversation has ended",
  }[callState];
  const clock = `${Math.floor(callSeconds / 60)
    .toString()
    .padStart(2, "0")}:${(callSeconds % 60).toString().padStart(2, "0")}`;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#home" aria-label="VoiceAIDoctor home">
          <img className="brand-logo" src="/voiceaidoctor-logo.svg" alt="" />
        </a>
        <div className="sidebar-label">WORKSPACE</div>
        <nav className="nav-links" aria-label="Main navigation">
          <a className="nav-link active" href="#overview"><Activity size={17} /> Overview</a>
          <a className="nav-link" href="#doctors"><Stethoscope size={17} /> Doctors</a>
          <a className="nav-link" href="#appointments"><CalendarDays size={17} /> Appointments</a>
        </nav>
        <div className="sidebar-bottom">
          <span className="sidebar-seal"><ShieldCheck size={17} /></span>
          <p>Care that starts<br />with a conversation.</p>
          <span className="sidebar-version">APPOINTMENT DESK <span>v1.0</span></span>
        </div>
      </aside>

      <main className="main-content" id="overview" aria-busy={isLoading}>
        <header className="topbar">
          <div className="breadcrumb"><span>VoiceAIDoctor</span><span className="crumb-slash">/</span> Appointment desk</div>
          <div className="topbar-right">
            <span className={`service-status ${health?.voice_ready ? "ready" : "not-ready"}`}>
              <i /> {health?.voice_ready ? "Voice assistant ready" : "Voice setup needed"}
            </span>
            <span className="avatar">VA</span>
          </div>
        </header>

        <div className="page-wrap">
          <section className="welcome-row">
            <div>
              <div className="eyebrow"><span className="eyebrow-dot" /> YOUR CLINIC, AT A GLANCE</div>
              <h1>A calmer way to <span>care.</span></h1>
              <p className="welcome-copy">Your appointment desk is ready. Let’s make every conversation count.</p>
            </div>
            <button className="primary-button try-call" onClick={openCallSetup} disabled={callOpen || Boolean(health && !health.voice_ready)}>
              <Phone size={16} fill="currentColor" /> Try a call <span className="button-arrow">↗</span>
            </button>
          </section>

          {error && <div className="alert" role="alert"><CircleHelp size={17} /> {error}<button onClick={() => setError("")} aria-label="Dismiss error"><X size={15} /></button></div>}
          {notice && <div className="notice" role="status"><Check size={16} /> {notice}</div>}
          {!health?.voice_ready && health && (
            <div className="setup-note">
              <CircleHelp size={17} />
              <span><strong>Voice keys not configured.</strong> Add the required Groq and ElevenLabs values to <code>.env</code> to enable browser calls. You can still add and manage mock data.</span>
              <span className="setup-gap" />
              {health.missing_voice_settings?.length || 0} missing
            </div>
          )}

          {isLoading && <p className="loading-message" role="status">Loading your clinic workspace…</p>}
          <section className="metric-grid" aria-label="Clinic statistics">
            <article className="metric-card">
              <div className="metric-heading"><span>AVAILABLE SLOTS</span><span className="metric-icon mint"><CalendarDays size={17} /></span></div>
              <div className="metric-value">{availableCount}<span className="metric-unit"> open</span></div>
              <div className="metric-foot"><span className="metric-accent">Ready to book</span> across all doctors</div>
            </article>
            <article className="metric-card">
              <div className="metric-heading"><span>UPCOMING APPOINTMENTS</span><span className="metric-icon peach"><UsersRound size={17} /></span></div>
              <div className="metric-value">{upcomingAppointments.length}<span className="metric-unit"> booked</span></div>
              <div className="metric-foot"><span className="metric-accent peach-text">Live schedule</span> updates automatically</div>
            </article>
            <article className="metric-card next-visit-card">
              <div className="metric-heading"><span>NEXT AVAILABLE</span><span className="metric-icon lavender"><Clock3 size={17} /></span></div>
              <div className="metric-value next-visit-value">{nextSlot ? formatDate(nextSlot.starts_at) : "—"}</div>
              <div className="metric-foot">{nextSlot ? `${formatTime(nextSlot.starts_at)} · ${dashboard.doctors.find((doctor) => doctor.available_slots.some((slot) => slot.id === nextSlot.id))?.name}` : "Add a doctor and an available slot"}</div>
            </article>
          </section>

          <section className="content-grid">
            <div className="section-panel doctors-panel" id="doctors">
              <div className="panel-heading">
                <div><div className="eyebrow section-eyebrow">YOUR CARE TEAM</div><h2>Doctors & availability</h2></div>
                <button className="quiet-button" onClick={() => setShowDoctorForm((visible) => !visible)}>
                  <Plus size={16} /> Add doctor
                </button>
              </div>
              {showDoctorForm && (
                <form className="inline-form doctor-form" onSubmit={(event) => {
                  const form = event.currentTarget;
                  const values = new FormData(form);
                  submitForm(event, "/api/doctors", { name: values.get("name"), specialty: values.get("specialty") }, () => {
                    form.reset();
                    setShowDoctorForm(false);
                  });
                }}>
                  <label>Doctor’s name<input name="name" placeholder="Dr. Jamie Chen" required minLength="3" maxLength="100" /></label>
                  <label>Specialty<input name="specialty" placeholder="Family medicine" required minLength="2" maxLength="100" /></label>
                  <button className="primary-button small-button" disabled={busy}>Save doctor</button>
                </form>
              )}
              <div className="doctor-list">
                {dashboard.doctors.map((doctor, index) => (
                  <article className="doctor-card" key={doctor.id}>
                    <div className={`doctor-avatar avatar-${index % 4}`}>{initials(doctor.name)}</div>
                    <div className="doctor-details">
                      <div className="doctor-title-row"><h3>{doctor.name}</h3></div>
                      <p className="specialty">{doctor.specialty}</p>
                      <div className="slot-row">
                        {doctor.available_slots.length ? doctor.available_slots.slice(0, 3).map((slot) => (
                          <span className="slot-chip" key={slot.id}><span />{formatDate(slot.starts_at)} · {formatTime(slot.starts_at)}</span>
                        )) : <span className="empty-slots">No open times yet</span>}
                      </div>
                      {openSlotForm === doctor.id && <form className="inline-form slot-form" onSubmit={(event) => {
                        const form = event.currentTarget;
                        const values = new FormData(form);
                        submitForm(event, `/api/doctors/${doctor.id}/slots`, {
                          starts_at: new Date(`${values.get("date")}T${values.get("time")}`).toISOString(),
                          duration_minutes: Number(values.get("duration")),
                        }, () => {
                          form.reset();
                          setOpenSlotForm("");
                        });
                      }}>
                        <label>Date<input type="date" name="date" required min={new Date().toISOString().slice(0, 10)} defaultValue={new Date(Date.now() + 86400000).toISOString().slice(0, 10)} /></label>
                        <label>Time<input type="time" name="time" required defaultValue="10:00" /></label>
                        <label>Minutes<select name="duration" defaultValue="30"><option value="30">30 min</option><option value="45">45 min</option><option value="60">60 min</option></select></label>
                        <button className="primary-button small-button" disabled={busy}>Add slot</button>
                      </form>}
                    </div>
                    <button className="doctor-menu" onClick={() => setOpenSlotForm((current) => current === doctor.id ? "" : doctor.id)} aria-label={`Add availability for ${doctor.name}`} title="Add availability"><Plus size={17} /></button>
                  </article>
                ))}
                {!dashboard.doctors.length && <div className="empty-state">Your care team will appear here once you add a doctor.</div>}
              </div>
              <div className="panel-footer"><span>{dashboard.doctors.length} {dashboard.doctors.length === 1 ? "doctor" : "doctors"} on your care team</span><span className="live-refresh"><i /> Updates live</span></div>
            </div>

            <div className="section-panel appointments-panel" id="appointments">
              <div className="panel-heading">
                <div><div className="eyebrow section-eyebrow">CLINIC SCHEDULE</div><h2>Appointments</h2></div>
                <button className="quiet-button" onClick={() => setShowBookingForm((visible) => !visible)} aria-expanded={showBookingForm}>
                  <Plus size={16} /> New appointment
                </button>
              </div>
              {showBookingForm && <form className="booking-form" onSubmit={(event) => {
                const form = event.currentTarget;
                const values = new FormData(form);
                const [doctorId, slotId] = values.get("slot").split("|");
                submitForm(event, "/api/appointments", {
                  phone_number: values.get("phone"),
                  doctor_id: doctorId,
                  slot_id: slotId,
                  phone_confirmed: values.get("confirmed") === "on",
                }, () => {
                  form.reset();
                  setShowBookingForm(false);
                });
              }}>
                <label>Caller’s phone number<input name="phone" type="tel" placeholder="+1 555 014 8820" required minLength="7" maxLength="20" /></label>
                <label>Doctor & time<select name="slot" required defaultValue="">
                  <option value="" disabled>Select an open appointment</option>
                  {dashboard.doctors.flatMap((doctor) => doctor.available_slots.map((slot) => (
                    <option key={slot.id} value={`${doctor.id}|${slot.id}`}>{doctor.name} · {formatDate(slot.starts_at)} at {formatTime(slot.starts_at)}</option>
                  )))}
                </select></label>
                <label className="confirmation-check"><input type="checkbox" name="confirmed" required /> Phone number read back and confirmed</label>
                <button className="primary-button small-button" disabled={busy || availableCount === 0}>Save test booking</button>
                {availableCount === 0 && <span className="form-hint">Add an available slot first.</span>}
              </form>}
              <div className="appointment-filters" role="group" aria-label="Filter appointments">
                <button className="filter-button" aria-pressed={appointmentFilter === "upcoming"} onClick={() => setAppointmentFilter("upcoming")}>
                  Upcoming <span>{upcomingAppointments.length}</span>
                </button>
                <button className="filter-button" aria-pressed={appointmentFilter === "all"} onClick={() => setAppointmentFilter("all")}>
                  All <span>{dashboard.appointments.length}</span>
                </button>
                <button className="filter-button" aria-pressed={appointmentFilter === "cancelled"} onClick={() => setAppointmentFilter("cancelled")}>
                  Cancelled <span>{cancelledAppointments.length}</span>
                </button>
              </div>
              <div className="appointment-list" aria-live="polite">
                {filteredAppointments.map((appointment) => (
                  <article className={`appointment-row ${appointment.status === "cancelled" ? "cancelled" : ""} ${highlightedAppointmentId === appointment.id ? "recently-updated" : ""}`} key={appointment.id}>
                    <div className="appointment-date"><span>{formatDate(appointment.starts_at).split(" ")[0]}</span><strong>{new Date(appointment.starts_at).getDate()}</strong></div>
                    <div className="appointment-info"><h3>{appointment.doctor_name}</h3><p>{appointment.specialty} <span>·</span> {formatTime(appointment.starts_at)}</p><p className="appointment-phone" title="Phone number hidden for privacy">{displayPhone(appointment.phone_number)}</p></div>
                    <div className="appointment-actions"><span className={`status-pill ${appointment.status}`}>{appointment.status === "booked" ? "Confirmed" : "Cancelled"}</span>{appointment.status === "booked" && <button className="cancel-button" onClick={() => cancelAppointment(appointment)} aria-label={`Cancel appointment with ${appointment.doctor_name}`}>Cancel</button>}</div>
                  </article>
                ))}
                {!filteredAppointments.length && (
                  <div className="empty-state appointment-empty">
                    <span className="empty-calendar"><CalendarDays size={21} /></span>
                    <strong>{appointmentFilter === "cancelled" ? "No cancelled appointments" : appointmentFilter === "upcoming" ? "No upcoming appointments" : "No appointments yet"}</strong>
                    <span>{appointmentFilter === "upcoming" ? "New bookings will appear here." : "Appointments made during a call or from this dashboard will appear here."}</span>
                  </div>
                )}
              </div>
              <div className="panel-footer"><span>{upcomingAppointments.length} upcoming {upcomingAppointments.length === 1 ? "visit" : "visits"}</span><span className="secure-note"><ShieldCheck size={14} /> Phone numbers masked</span></div>
            </div>
          </section>

          <footer className="page-footer"><span>© {new Date().getFullYear()} VoiceAIDoctor. All rights reserved.</span><span><span className="footer-dot" /> Appointment desk is online</span></footer>
        </div>
      </main>

      {callOpen && (
        <div className="call-overlay" role="dialog" aria-modal="true" aria-labelledby="call-title">
          <div className="call-card">
            <button className="call-close" onClick={callState === "setup" ? cancelCallSetup : callState === "ended" ? closeCallSummary : endCall} aria-label={callState === "setup" ? "Cancel call setup" : callState === "ended" ? "Close call summary" : "End call"}><X size={19} /></button>
            <span className="call-label"><span className={callState === "connected" ? "pulse-dot" : callState === "ended" ? "pulse-dot ended" : "pulse-dot connecting"} /> {callStatus}</span>
            <div className="call-avatar"><HeartPulse size={29} /></div>
            <h2 id="call-title">{callerName || (callState === "setup" && nameDraft.trim()) || "Caller"}</h2>
            <p className="call-identity">
              {callState === "setup"
                ? "Connected to VoiceAIDoctor · name is optional and display-only"
                : "VoiceAIDoctor · caller verified by spoken phone number"}
            </p>
            <p className="call-subtitle">{callSubtitle}</p>
            {callState === "setup" ? (
              <form className="call-setup" onSubmit={(event) => {
                event.preventDefault();
                setCallerName(nameDraft.trim());
                void startCall();
              }}>
                <label htmlFor="caller-display-name">Caller name (optional)</label>
                <input
                  id="caller-display-name"
                  value={nameDraft}
                  onChange={(event) => setNameDraft(event.target.value)}
                  maxLength={60}
                  autoComplete="name"
                  placeholder="Name shown on this call"
                />
                <p>This label is not used to verify identity. The caller confirms their spoken phone number before booking or cancellation.</p>
                <button className="primary-button" type="submit">Start voice call</button>
              </form>
            ) : callState === "ended" ? (
              <div className="call-ended" role="status">
                <span className="call-ended-icon"><Check size={19} /></span>
                <strong>Call ended</strong>
                <span>Your call lasted {clock}</span>
                <button className="quiet-button" onClick={closeCallSummary}>Return to dashboard</button>
              </div>
            ) : (
              <>
                <div className="call-time">{callState === "connected" ? clock : "•• : ••"}</div>
                <div className="voice-visualizer" ref={visualizerRef} aria-hidden="true">{Array.from({ length: 31 }, (_, index) => <i key={index} style={{ "--bar": `${12 + ((index * 37) % 29)}px`, "--voice-level": "0.08" }} />)}</div>
                {callNotice && (
                  <div className={`call-confirmation ${callNotice.type === "appointment_booked" ? "booked" : "cancelled"}`} role="status">
                    <span className="call-confirmation-icon"><Check size={16} /></span>
                    <span>
                      <strong>{callNotice.type === "appointment_booked" ? "Appointment booked" : "Appointment cancelled"}</strong>
                      <span>{callNotice.doctor} · {formatDate(callNotice.starts_at)} at {formatTime(callNotice.starts_at)}</span>
                    </span>
                  </div>
                )}
                {visualizerWarning && <p className="call-warning" role="status">{visualizerWarning}</p>}
                {callError && <p className="call-error" role="alert">{callError}</p>}
                {audioPlaybackBlocked && (
                  <button
                    className="quiet-button"
                    onClick={() => remoteAudioRef.current?.play()
                      .then(() => setAudioPlaybackBlocked(false))
                      .catch(() => setAudioPlaybackBlocked(true))}
                  >
                    <AudioLines size={16} /> Enable assistant audio
                  </button>
                )}
                <div className="transcript-box">
                  <div className="transcript-head"><span>LIVE TRANSCRIPT</span><span><Radio size={13} /> {callState === "connected" ? "Live" : callState === "failed" ? "Unavailable" : callState === "ringing" ? "Ringing" : "Connecting"}</span></div>
                  <div className="transcript-messages">
                    {transcript.map((entry, index) => <p className={`transcript-line ${entry.speaker}`} key={`${index}-${entry.at}`}><span>{entry.speaker === "caller" ? "YOU" : "ASSISTANT"}</span>{entry.text}</p>)}
                    {transcript.length === 0 && <p className="transcript-placeholder">{callState === "connected" ? "Your conversation will appear here…" : callState === "failed" ? "No transcript was recorded for this call." : "Waiting for a secure connection…"}</p>}
                    <div ref={transcriptEndRef} />
                  </div>
                </div>
                <div className="call-controls">
                  <button className={`control-button ${muted ? "muted" : ""}`} onClick={toggleMute} aria-label={muted ? "Unmute microphone" : "Mute microphone"}>{muted ? <MicOff size={19} /> : <Mic size={19} />}<span>{muted ? "Unmute" : "Mute"}</span></button>
                  <button className="end-call-button" onClick={endCall} aria-label="End call"><Phone size={21} fill="currentColor" /></button>
                  <button
                    className={`control-button ${speakerEnabled ? "" : "muted"}`}
                    onClick={toggleSpeaker}
                    aria-label={speakerEnabled ? "Mute assistant audio" : "Enable assistant audio"}
                    aria-pressed={!speakerEnabled}
                  >
                    {speakerEnabled ? <Volume2 size={19} /> : <VolumeX size={19} />}
                    <span>{speakerEnabled ? "Speaker on" : "Speaker off"}</span>
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
