import { useEffect, useState } from "react";

// Minimal fetch hook: data, error, loading. Re-runs when `key` changes.
export function useData<T>(load: () => Promise<T>, key: string) {
  const [state, setState] = useState<{ data?: T; error?: string; loading: boolean }>({
    loading: true,
  });
  useEffect(() => {
    let cancelled = false;
    setState({ loading: true });
    load()
      .then((data) => !cancelled && setState({ data, loading: false }))
      .catch((e: Error) => !cancelled && setState({ error: e.message, loading: false }));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return state;
}
