"use client";

import { useEffect, useRef, useState } from "react";
import { Check, LoaderCircle, Mic, X } from "lucide-react";
import { errorMessage, transcribeAudio } from "@/lib/api";

const MIME_TYPES = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus", "audio/webm"];
const BAR_COUNT = 34;
const QUIET_LEVELS = Array.from({ length: BAR_COUNT }, (_, index) => 10 + (index % 4) * 2);

function supportedMimeType() {
  if (typeof MediaRecorder === "undefined") return "";
  return MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) ?? "";
}

function durationLabel(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

export function VoiceInputButton({ disabled = false, onTranscript }: {
  disabled?: boolean;
  onTranscript: (text: string) => void;
}) {
  const [phase, setPhase] = useState<"idle" | "recording" | "transcribing">("idle");
  const [seconds, setSeconds] = useState(0);
  const [levels, setLevels] = useState(QUIET_LEVELS);
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<"success" | "error">("success");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const cancelledRef = useRef(false);
  const audioContextRef = useRef<AudioContext | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const mountedRef = useRef(true);

  const stopVisualiser = () => {
    if (animationFrameRef.current !== null) cancelAnimationFrame(animationFrameRef.current);
    animationFrameRef.current = null;
    const context = audioContextRef.current;
    audioContextRef.current = null;
    if (context && context.state !== "closed") void context.close();
    setLevels(QUIET_LEVELS);
  };

  const releaseStream = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    stopVisualiser();
  };

  const startVisualiser = (stream: MediaStream) => {
    const context = new AudioContext();
    const analyser = context.createAnalyser();
    analyser.fftSize = 128;
    analyser.smoothingTimeConstant = 0.76;
    context.createMediaStreamSource(stream).connect(analyser);
    audioContextRef.current = context;
    const samples = new Uint8Array(analyser.frequencyBinCount);
    const draw = () => {
      analyser.getByteFrequencyData(samples);
      setLevels(Array.from({ length: BAR_COUNT }, (_, index) => {
        const sample = samples[Math.floor(index * samples.length / BAR_COUNT)] ?? 0;
        return Math.max(10, Math.min(100, 10 + sample * 0.43));
      }));
      animationFrameRef.current = requestAnimationFrame(draw);
    };
    draw();
  };

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const recorder = recorderRef.current;
      if (recorder) recorder.onstop = null;
      if (recorder?.state === "recording") recorder.stop();
      streamRef.current?.getTracks().forEach((track) => track.stop());
      if (animationFrameRef.current !== null) cancelAnimationFrame(animationFrameRef.current);
      const context = audioContextRef.current;
      if (context && context.state !== "closed") void context.close();
    };
  }, []);

  useEffect(() => {
    if (phase !== "recording") return;
    const timer = window.setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [phase]);

  const finishRecording = async (type: string) => {
    releaseStream();
    if (!mountedRef.current) return;
    if (cancelledRef.current) {
      chunksRef.current = [];
      setPhase("idle");
      setMessage("");
      return;
    }
    const audio = new Blob(chunksRef.current, { type: type || "audio/webm" });
    chunksRef.current = [];
    if (!audio.size) {
      setPhase("idle");
      setMessageTone("error");
      setMessage("没有录到声音，请重试");
      return;
    }

    setPhase("transcribing");
    try {
      const result = await transcribeAudio(audio);
      if (!mountedRef.current) return;
      const text = result.text.trim();
      if (!text) throw new Error("没有识别到可用文字");
      onTranscript(text);
      setMessageTone("success");
      setMessage("已添加到输入框");
      window.setTimeout(() => {
        if (mountedRef.current) setMessage((current) => current === "已添加到输入框" ? "" : current);
      }, 1800);
    } catch (cause) {
      if (mountedRef.current) {
        setMessageTone("error");
        setMessage(errorMessage(cause, "语音转写失败"));
      }
    } finally {
      if (mountedRef.current) setPhase("idle");
    }
  };

  const startRecording = async () => {
    setMessage("");
    if (typeof MediaRecorder === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setMessageTone("error");
      setMessage("当前浏览器不支持录音");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      if (!mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const mimeType = supportedMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      streamRef.current = stream;
      recorderRef.current = recorder;
      chunksRef.current = [];
      cancelledRef.current = false;
      recorder.ondataavailable = (event) => { if (event.data.size) chunksRef.current.push(event.data); };
      recorder.onstop = () => void finishRecording(recorder.mimeType || mimeType);
      recorder.onerror = () => {
        releaseStream();
        if (mountedRef.current) {
          setPhase("idle");
          setMessageTone("error");
          setMessage("录音失败，请重试");
        }
      };
      recorder.start(250);
      startVisualiser(stream);
      setSeconds(0);
      setPhase("recording");
    } catch (cause) {
      releaseStream();
      const denied = cause instanceof DOMException && (cause.name === "NotAllowedError" || cause.name === "SecurityError");
      setMessageTone("error");
      setMessage(denied ? "请允许浏览器使用麦克风" : errorMessage(cause, "无法启动录音"));
    }
  };

  const stopRecording = () => {
    if (recorderRef.current?.state !== "recording") return;
    setPhase("transcribing");
    recorderRef.current.stop();
  };

  const cancelRecording = () => {
    cancelledRef.current = true;
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    else {
      releaseStream();
      setPhase("idle");
    }
  };

  if (phase === "idle") return <span className="voice-input-control idle">
    {message && <span className={`voice-input-message ${messageTone}`} role="status" title={message}>{message}</span>}
    <button className="voice-input-button" type="button" aria-label="语音输入" title="语音输入" disabled={disabled} onClick={() => void startRecording()}><Mic size={16} /></button>
  </span>;

  return <span className={`voice-input-control expanded ${phase}`} role="group" aria-label={phase === "recording" ? "正在录音" : "正在识别语音"}>
    {phase === "recording" ? <button className="voice-input-action cancel" type="button" aria-label="取消录音" title="取消" onClick={cancelRecording}><X size={17} /></button> : <LoaderCircle size={17} className="spin voice-input-loader" />}
    <span className="voice-input-waveform" aria-hidden="true">{levels.map((level, index) => <i key={index} style={{ height: `${phase === "transcribing" ? 22 + (index % 5) * 8 : level}%` }} />)}</span>
    <span className="voice-input-state" aria-live="polite">{phase === "recording" ? <><strong>{durationLabel(seconds)}</strong><small>正在聆听</small></> : <><strong>识别中</strong><small>正在生成文字…</small></>}</span>
    {phase === "recording" && <button className="voice-input-action finish" type="button" aria-label={`完成录音，已录制 ${durationLabel(seconds)}`} title="完成" onClick={stopRecording}><Check size={17} /></button>}
  </span>;
}
