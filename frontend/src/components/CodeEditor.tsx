import { useEffect, useRef, type ReactNode } from "react";
import Editor, { loader, type OnMount } from "@monaco-editor/react";
import * as monaco from "monaco-editor/esm/vs/editor/editor.api.js";
import "monaco-editor/esm/vs/basic-languages/python/python.contribution.js";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";

self.MonacoEnvironment = {
  getWorker() {
    return new EditorWorker();
  },
};
loader.config({ monaco });

export type EditorInstance = monaco.editor.IStandaloneCodeEditor;

export interface EditorTheme {
  name: string;
  data: monaco.editor.IStandaloneThemeData;
}

interface Props {
  value: string;
  onChange: (value: string) => void;
  readOnly: boolean;
  onSubmit: () => void;
  theme: EditorTheme;
  fontFamily: string;
  onEditor?: (editor: EditorInstance) => void;
  loading?: ReactNode;
}

export default function CodeEditor({
  value,
  onChange,
  readOnly,
  onSubmit,
  theme,
  fontFamily,
  onEditor,
  loading,
}: Props) {
  const submit = useRef(onSubmit);
  const currentValue = useRef(value);
  currentValue.current = value;
  useEffect(() => {
    submit.current = onSubmit;
  }, [onSubmit]);

  const onMount: OnMount = (editor, instance) => {
    instance.editor.defineTheme(theme.name, theme.data);
    instance.editor.setTheme(theme.name);
    editor.addCommand(instance.KeyMod.CtrlCmd | instance.KeyCode.Enter, () =>
      submit.current(),
    );
    editor.setValue(currentValue.current);
    editor.focus();
    onEditor?.(editor);
  };

  return (
    <Editor
      language="python"
      path="file:///solution.py"
      keepCurrentModel
      value={value}
      onChange={(value) => onChange(value ?? "")}
      onMount={onMount}
      options={{
        readOnly,
        fontSize: 14,
        fontFamily,
        fontLigatures: true,
        tabSize: 4,
        wordWrap: "on",
        // Monaco 0.55 leaves occurrence requests unhandled when editors close.
        occurrencesHighlight: "off",
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        renderLineHighlight: "line",
        cursorBlinking: "smooth",
        cursorSmoothCaretAnimation: "on",
        smoothScrolling: true,
        padding: { top: 12, bottom: 12 },
        overviewRulerBorder: false,
        hideCursorInOverviewRuler: true,
        scrollbar: { verticalScrollbarSize: 6, horizontalScrollbarSize: 6 },
        bracketPairColorization: { enabled: true },
      }}
      loading={loading}
    />
  );
}
