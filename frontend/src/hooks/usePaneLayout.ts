import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";

export type Splitter = "problem" | "chat" | "results";

const STORAGE_KEY = "leetrace:layout";
const MIN_PROBLEM = 260;
const MIN_EDITOR = 380;
const MIN_CHAT = 240;
const MAX_CHAT = 520;
const MIN_RESULTS = 90;
const MIN_EDITOR_HEIGHT = 160;
const WIDE_QUERY = "(min-width: 1024px)";

interface Sizes {
  editorWidth: number;
  chatWidth: number;
  resultsHeight: number;
}

function loadSizes(): Sizes {
  const fallback = {
    editorWidth: Math.round(window.innerWidth * 0.5),
    chatWidth: 320,
    resultsHeight: 220,
  };
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    return { ...fallback, ...(saved as Partial<Sizes> | null) };
  } catch {
    return fallback;
  }
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

/**
 * Sizes for the room's problem | editor (+ results) | chat panes.
 * The problem pane takes whatever width is left, so opening the chat
 * contracts it while the editor keeps its width.
 */
export function usePaneLayout({
  messageCount,
  onChatClosed,
}: {
  messageCount: number;
  onChatClosed?: () => void;
}) {
  const [container, containerRef] = useState<HTMLDivElement | null>(null);
  const columnRef = useRef<HTMLDivElement>(null);
  const chatInputRef = useRef<HTMLTextAreaElement & HTMLInputElement>(null);
  const [sizes, setSizes] = useState(loadSizes);
  const [containerWidth, setContainerWidth] = useState(0);
  const [dragging, setDragging] = useState<Splitter | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [seen, setSeen] = useState(messageCount);
  const [wide, setWide] = useState(() => window.matchMedia(WIDE_QUERY).matches);
  const closed = useRef(onChatClosed);
  closed.current = onChatClosed;

  useEffect(() => {
    const query = window.matchMedia(WIDE_QUERY);
    const update = () => setWide(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sizes));
  }, [sizes]);

  useEffect(() => {
    if (!container) return;
    const observer = new ResizeObserver(([entry]) =>
      setContainerWidth(entry?.contentRect.width ?? 0),
    );
    observer.observe(container);
    return () => observer.disconnect();
  }, [container]);

  useEffect(() => {
    if (chatOpen) setSeen(messageCount);
  }, [chatOpen, messageCount]);

  const toggleChat = useCallback((open?: boolean) => {
    setChatOpen((current) => open ?? !current);
  }, []);

  const wasOpen = useRef(false);
  useEffect(() => {
    if (chatOpen) requestAnimationFrame(() => chatInputRef.current?.focus());
    else if (wasOpen.current) requestAnimationFrame(() => closed.current?.());
    wasOpen.current = chatOpen;
  }, [chatOpen]);

  useEffect(() => {
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (
        event.ctrlKey &&
        !event.altKey &&
        !event.metaKey &&
        !event.shiftKey &&
        event.key.toLowerCase() === "d"
      ) {
        event.preventDefault();
        event.stopPropagation();
        toggleChat();
      } else if (event.key === "Escape" && chatOpen) {
        toggleChat(false);
      }
    }
    // Capture phase so Monaco's own Ctrl+D binding never sees the event.
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [chatOpen, toggleChat]);

  const chatWidth = clamp(
    sizes.chatWidth,
    MIN_CHAT,
    containerWidth ? Math.min(MAX_CHAT, containerWidth - MIN_PROBLEM - MIN_EDITOR - 24) : MAX_CHAT,
  );
  const chatSpace = chatOpen ? chatWidth + 16 : 52;
  const maxEditor = containerWidth
    ? containerWidth - MIN_PROBLEM - chatSpace - 8
    : Infinity;
  const editorWidth = clamp(sizes.editorWidth, MIN_EDITOR, maxEditor);

  function resize(splitter: Splitter, start: Sizes, delta: number) {
    const width = container?.clientWidth ?? window.innerWidth;
    const height = columnRef.current?.clientHeight ?? window.innerHeight;
    setSizes((current) => {
      if (splitter === "problem")
        return {
          ...current,
          editorWidth: clamp(
            start.editorWidth - delta,
            MIN_EDITOR,
            width - MIN_PROBLEM - chatSpace - 8,
          ),
        };
      if (splitter === "chat")
        return {
          ...current,
          chatWidth: clamp(
            start.chatWidth - delta,
            MIN_CHAT,
            Math.min(MAX_CHAT, width - MIN_PROBLEM - editorWidth - 24),
          ),
        };
      return {
        ...current,
        resultsHeight: clamp(
          start.resultsHeight - delta,
          MIN_RESULTS,
          height - MIN_EDITOR_HEIGHT,
        ),
      };
    });
  }

  function handleProps(splitter: Splitter) {
    const vertical = splitter !== "results";
    return {
      role: "separator",
      tabIndex: 0,
      "aria-orientation": vertical ? "vertical" : "horizontal",
      "aria-label": `Resize ${splitter} pane`,
      "data-dragging": dragging === splitter || undefined,
      onPointerDown(event: PointerEvent<HTMLElement>) {
        if (event.button !== 0) return;
        event.preventDefault();
        const target = event.currentTarget;
        target.setPointerCapture(event.pointerId);
        const origin = vertical ? event.clientX : event.clientY;
        const start = { ...sizes, editorWidth, chatWidth };
        setDragging(splitter);
        document.body.style.cursor = vertical ? "col-resize" : "row-resize";
        document.body.style.userSelect = "none";
        const move = (moveEvent: globalThis.PointerEvent) =>
          resize(
            splitter,
            start,
            (vertical ? moveEvent.clientX : moveEvent.clientY) - origin,
          );
        const stop = () => {
          target.removeEventListener("pointermove", move);
          target.removeEventListener("pointerup", stop);
          target.removeEventListener("pointercancel", stop);
          document.body.style.cursor = "";
          document.body.style.userSelect = "";
          setDragging(null);
        };
        target.addEventListener("pointermove", move);
        target.addEventListener("pointerup", stop);
        target.addEventListener("pointercancel", stop);
      },
      onKeyDown(event: KeyboardEvent<HTMLElement>) {
        const keys = vertical
          ? { ArrowLeft: -24, ArrowRight: 24 }
          : { ArrowUp: -24, ArrowDown: 24 };
        const delta = keys[event.key as keyof typeof keys];
        if (delta === undefined) return;
        event.preventDefault();
        resize(splitter, { ...sizes, editorWidth, chatWidth }, delta);
      },
    } as const;
  }

  return {
    containerRef,
    columnRef,
    chatInputRef,
    wide,
    dragging,
    editorWidth,
    chatWidth,
    resultsHeight: sizes.resultsHeight,
    chatOpen,
    toggleChat,
    unread: chatOpen ? 0 : Math.max(0, messageCount - seen),
    handleProps,
  };
}

export type PaneLayout = ReturnType<typeof usePaneLayout>;
