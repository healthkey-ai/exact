import { useCallback, useEffect, useMemo, useState } from "react";

import { PromopClient, type PromopPatientSummary } from "./promopClient";

type Source = "promop-local" | "promop-staging";

const SOURCE_STORAGE_KEY = "exact-harness-source";
const VALID_SOURCES: readonly Source[] = ["promop-local", "promop-staging"] as const;

function loadSource(): Source {
  try {
    const v = localStorage.getItem(SOURCE_STORAGE_KEY);
    if (v && (VALID_SOURCES as readonly string[]).includes(v)) return v as Source;
  } catch {
    /* ignore */
  }
  return "promop-local";
}

function saveSource(s: Source): void {
  try {
    localStorage.setItem(SOURCE_STORAGE_KEY, s);
  } catch {
    /* ignore */
  }
}

interface Props {
  /** Called when the user picks a patient. The harness wires this to
   *  TrialMatches's `personId` prop so EXACT fetches the row server-side
   *  via the resolver added in #102. */
  onSelect: (personId: number | null) => void;
  selectedPersonId: number | null;
}

export function PromopPicker({ onSelect, selectedPersonId }: Props) {
  const [source, setSource] = useState<Source>(() => loadSource());
  const [needsLogin, setNeedsLogin] = useState(false);
  const [patients, setPatients] = useState<PromopPatientSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Reachability + auth status + patient list — driven by a single
  // effect so the UI never blinks between "no patients" and "logged out".
  const baseFor = useCallback(
    (s: Source) => (s === "promop-local" ? "/promop-local" : "/promop-staging"),
    [],
  );

  const client = useMemo(() => new PromopClient(baseFor(source)), [source, baseFor]);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setLoading(true);
    setPatients(null);
    setNeedsLogin(false);

    (async () => {
      const reachable = await client.ping();
      if (cancelled) return;
      if (!reachable) {
        setError(
          `PROMOP at ${baseFor(source)} is not reachable. Is the backend running?`,
        );
        setLoading(false);
        return;
      }
      try {
        const list = await client.listPatients();
        if (cancelled) return;
        setPatients(list);
      } catch (e) {
        if (cancelled) return;
        const status = (e as { response?: { status?: number } }).response?.status;
        if (status === 401 || status === 403) {
          setNeedsLogin(true);
        } else {
          setError((e as Error).message);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [client, baseFor, source]);

  const handleSource = (s: Source) => {
    setSource(s);
    saveSource(s);
    onSelect(null);
  };

  return (
    <div style={{ padding: "1rem", border: "1px solid #e5e7eb", borderRadius: "0.5rem", background: "#fff" }}>
      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.75rem" }}>
        {VALID_SOURCES.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => handleSource(s)}
            style={{
              padding: "0.25rem 0.625rem",
              borderRadius: "0.25rem",
              border: "1px solid #d1d5db",
              background: source === s ? "#0c5fc0" : "#fff",
              color: source === s ? "#fff" : "#111827",
              fontSize: "0.75rem",
              cursor: "pointer",
            }}
          >
            {s === "promop-local" ? "Local" : "Staging"}
          </button>
        ))}
      </div>

      {loading ? (
        <p style={{ color: "#6b7280", fontSize: "0.875rem", margin: 0 }}>
          Loading patients…
        </p>
      ) : null}

      {needsLogin ? (
        <PromopInlineLogin
          client={client}
          onLoggedIn={() => {
            // Re-list after login. If the post-login fetch also fails
            // (token raced, network blip, 5xx), surface the error and
            // re-arm the login form so the user can try again — without
            // the catch the spinner would stay forever.
            setNeedsLogin(false);
            setLoading(true);
            client
              .listPatients()
              .then((list) => {
                setPatients(list);
                setLoading(false);
              })
              .catch((e) => {
                const status = (e as { response?: { status?: number } }).response
                  ?.status;
                if (status === 401 || status === 403) {
                  setNeedsLogin(true);
                } else {
                  setError((e as Error).message);
                }
                setLoading(false);
              });
          }}
        />
      ) : null}

      {error ? (
        <p style={{ color: "#991b1b", fontSize: "0.875rem" }}>{error}</p>
      ) : null}

      {patients?.length === 0 ? (
        <p style={{ color: "#6b7280", fontSize: "0.875rem" }}>
          No patients available on this PROMOP backend.
        </p>
      ) : null}

      {patients && patients.length > 0 ? (
        <ul style={{ listStyle: "none", padding: 0, margin: 0, maxHeight: "20rem", overflowY: "auto" }}>
          {patients.map((p) => (
            <li key={p.id ?? p.person_id} style={{ borderBottom: "1px solid #f3f4f6" }}>
              <button
                type="button"
                onClick={() => onSelect(p.person_id)}
                style={{
                  width: "100%",
                  textAlign: "left",
                  padding: "0.5rem",
                  background: selectedPersonId === p.person_id ? "#eff6ff" : "transparent",
                  border: "none",
                  cursor: "pointer",
                  font: "inherit",
                }}
              >
                <div style={{ fontWeight: 500 }}>
                  #{p.person_id} {p.patient_name ? ` · ${p.patient_name}` : ""}
                </div>
                <div style={{ fontSize: "0.75rem", color: "#6b7280" }}>
                  {p.disease ?? "—"}
                  {p.stage ? ` · ${p.stage}` : ""}
                  {p.age != null ? ` · age ${p.age}` : ""}
                </div>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function PromopInlineLogin({
  client,
  onLoggedIn,
}: {
  client: PromopClient;
  onLoggedIn: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await client.login(username, password);
      onLoggedIn();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "0.375rem",
        marginBottom: "0.75rem",
      }}
    >
      <p style={{ margin: 0, fontSize: "0.75rem", color: "#6b7280" }}>
        PROMOP requires sign-in to list patients.
      </p>
      <label style={{ fontSize: "0.75rem", color: "#6b7280" }}>
        <span style={{ position: "absolute", left: "-9999px" }}>PROMOP username</span>
        <input
          type="text"
          placeholder="username"
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          style={{ width: "100%", padding: "0.375rem 0.5rem", border: "1px solid #d1d5db", borderRadius: "0.25rem" }}
        />
      </label>
      <label style={{ fontSize: "0.75rem", color: "#6b7280" }}>
        <span style={{ position: "absolute", left: "-9999px" }}>PROMOP password</span>
        <input
          type="password"
          placeholder="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={{ width: "100%", padding: "0.375rem 0.5rem", border: "1px solid #d1d5db", borderRadius: "0.25rem" }}
        />
      </label>
      {error ? (
        <div style={{ color: "#991b1b", fontSize: "0.75rem" }}>{error}</div>
      ) : null}
      <button
        type="submit"
        disabled={submitting}
        style={{
          padding: "0.375rem",
          background: submitting ? "#9ca3af" : "#0c5fc0",
          color: "#fff",
          border: "none",
          borderRadius: "0.25rem",
          cursor: submitting ? "not-allowed" : "pointer",
          fontSize: "0.75rem",
        }}
      >
        {submitting ? "Signing in…" : "Sign in to PROMOP"}
      </button>
    </form>
  );
}
