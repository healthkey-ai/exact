import { useState } from "react";

import { obtainExactToken } from "./exactAuth";

interface Props {
  onTokenObtained: (token: string) => void;
}

export function ExactLoginForm({ onTokenObtained }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const token = await obtainExactToken(username, password);
      onTokenObtained(token);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "0.5rem",
        maxWidth: "20rem",
        padding: "1rem",
        border: "1px solid #e5e7eb",
        borderRadius: "0.5rem",
        background: "#fff",
      }}
    >
      <h2 style={{ margin: 0, fontSize: "1rem" }}>Sign in to EXACT</h2>
      <p style={{ margin: 0, fontSize: "0.75rem", color: "#6b7280" }}>
        Calls <code>POST /api-token-auth/</code>. The token is stored in{" "}
        <code>localStorage</code>.
      </p>
      <label style={{ display: "flex", flexDirection: "column", fontSize: "0.75rem" }}>
        Username
        <input
          type="text"
          autoComplete="username"
          required
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          style={{
            padding: "0.375rem 0.5rem",
            border: "1px solid #d1d5db",
            borderRadius: "0.25rem",
          }}
        />
      </label>
      <label style={{ display: "flex", flexDirection: "column", fontSize: "0.75rem" }}>
        Password
        <input
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={{
            padding: "0.375rem 0.5rem",
            border: "1px solid #d1d5db",
            borderRadius: "0.25rem",
          }}
        />
      </label>
      {error ? (
        <div style={{ color: "#991b1b", fontSize: "0.75rem" }}>{error}</div>
      ) : null}
      <button
        type="submit"
        disabled={submitting}
        style={{
          padding: "0.5rem 0.75rem",
          background: submitting ? "#9ca3af" : "#0c5fc0",
          color: "#fff",
          border: "none",
          borderRadius: "0.25rem",
          cursor: submitting ? "not-allowed" : "pointer",
        }}
      >
        {submitting ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
