import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * 语音能力（FR-V1 语音转文字 / FR-V2 文字转语音）。
 *
 * 走浏览器原生 API 而不是服务端模型，原因很实际：
 *   - 零成本、零延迟，Chrome 的中文识别与合成质量足够日常问数使用；
 *   - 服务端 Whisper / TTS 需要额外的 API Key 与音频带宽，
 *     对"可选增强项"来说不划算；接口预留见 /api/v1/speech/*。
 * 浏览器不支持时组件会隐藏按钮并给出提示，不做静默降级。
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

function getRecognitionCtor(): any {
  if (typeof window === 'undefined') return null;
  return (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition || null;
}

export const STT_SUPPORTED = Boolean(getRecognitionCtor());
export const TTS_SUPPORTED =
  typeof window !== 'undefined' && 'speechSynthesis' in window;

/**
 * 语音转文字：识别结果通过 onResult 回填（最终识别），
 * 过程中会把 interim 结果也抛出来，让输入框有"正在听"的反馈。
 */
export function useSpeechRecognition(opts: {
  lang?: string;
  onResult: (text: string) => void;
  onInterim?: (text: string) => void;
  onError?: (msg: string) => void;
}) {
  const supported = STT_SUPPORTED;
  const [listening, setListening] = useState(false);
  const recRef = useRef<any>(null);
  const cbRef = useRef(opts);
  cbRef.current = opts;

  useEffect(() => {
    if (!supported) return;
    const Ctor = getRecognitionCtor();
    const rec = new Ctor();
    rec.lang = opts.lang ?? 'zh-CN';
    rec.continuous = false;
    rec.interimResults = true;
    rec.maxAlternatives = 1;

    rec.onresult = (e: any) => {
      let interim = '';
      let final = '';
      for (let i = e.resultIndex; i < e.results.length; i += 1) {
        const chunk = e.results[i][0].transcript;
        if (e.results[i].isFinal) final += chunk;
        else interim += chunk;
      }
      if (final) cbRef.current.onResult(final);
      if (interim) cbRef.current.onInterim?.(interim);
    };
    rec.onerror = (e: any) => {
      setListening(false);
      const msg =
        e.error === 'not-allowed'
          ? '麦克风权限被拒绝，请在浏览器地址栏允许后重试'
          : e.error === 'no-speech'
            ? '没有听到声音，请再试一次'
            : `识别失败：${e.error}`;
      cbRef.current.onError?.(msg);
    };
    rec.onend = () => setListening(false);

    recRef.current = rec;
    return () => {
      rec.abort();
      recRef.current = null;
    };
  }, [supported, opts.lang]);

  const start = useCallback(() => {
    if (!recRef.current) return;
    setListening(true);
    try {
      recRef.current.start();
    } catch {
      setListening(false);
    }
  }, []);

  const stop = useCallback(() => {
    recRef.current?.stop();
    setListening(false);
  }, []);

  const toggle = useCallback(() => {
    if (listening) stop();
    else start();
  }, [listening, start, stop]);

  return { supported, listening, start, stop, toggle };
}

/** 把长文本切成句子：整段喂给浏览器容易被截断，且中途无法自然停顿 */
function splitSentences(text: string): string[] {
  const parts = text.match(/[^。！？；!?\n]+[。！？；!?\n]?/g);
  const cleaned = (parts ?? [text]).map((s) => s.trim()).filter(Boolean);
  return cleaned.length ? cleaned : [text];
}

/** 文字转语音：支持暂停 / 继续 / 停止 */
export function useSpeechSynthesis(lang = 'zh-CN') {
  const supported = TTS_SUPPORTED;
  const [speaking, setSpeaking] = useState(false);
  const [paused, setPaused] = useState(false);
  const chunks = useRef<string[]>([]);
  const idx = useRef(0);

  const speakNext = useCallback(() => {
    if (idx.current >= chunks.current.length) {
      setSpeaking(false);
      setPaused(false);
      return;
    }
    const utter = new SpeechSynthesisUtterance(chunks.current[idx.current]);
    utter.lang = lang;
    utter.onend = () => {
      idx.current += 1;
      speakNext();
    };
    utter.onerror = () => {
      setSpeaking(false);
      setPaused(false);
    };
    window.speechSynthesis.speak(utter);
  }, [lang]);

  const speak = useCallback(
    (text: string) => {
      if (!supported || !text.trim()) return;
      window.speechSynthesis.cancel();
      chunks.current = splitSentences(text);
      idx.current = 0;
      setPaused(false);
      setSpeaking(true);
      speakNext();
    },
    [supported, speakNext],
  );

  const stop = useCallback(() => {
    window.speechSynthesis.cancel();
    chunks.current = [];
    setSpeaking(false);
    setPaused(false);
  }, []);

  const togglePause = useCallback(() => {
    if (paused) {
      window.speechSynthesis.resume();
      setPaused(false);
    } else {
      window.speechSynthesis.pause();
      setPaused(true);
    }
  }, [paused]);

  // 离开页面时必须停掉，否则朗读会一直延续到下一个页面
  useEffect(
    () => () => {
      if (typeof window !== 'undefined') window.speechSynthesis?.cancel();
    },
    [],
  );

  return { supported, speaking, paused, speak, stop, togglePause };
}
