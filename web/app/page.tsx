"use client";

import type { Session } from "@supabase/supabase-js";
import { useEffect, useMemo, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";

import { supabase, supabaseConfigured } from "../lib/supabase";

type ReferenceUpload = {
  id: string;
  file: File;
  previewUrl: string;
};

type RenderResponse = {
  job_id: string;
  status: string;
  video_url: string | null;
  message: string;
};

type Project = {
  id: string;
  name: string;
};

function fileSize(size: number) {
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export default function DirectorStudio() {
  const [references, setReferences] = useState<ReferenceUpload[]>([]);
  const [prompt, setPrompt] = useState("");
  const [title, setTitle] = useState("Episode 1 — Shot 1");
  const [duration, setDuration] = useState(4);
  const [aspectRatio, setAspectRatio] = useState("9:16");
  const [resolution, setResolution] = useState("Preview");
  const [seed, setSeed] = useState(42);
  const [status, setStatus] = useState("Ready for direction.");
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [isRendering, setIsRendering] = useState(false);
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authMode, setAuthMode] = useState<"sign-in" | "sign-up">("sign-in");
  const [authBusy, setAuthBusy] = useState(false);
  const [authMessage, setAuthMessage] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [newProjectName, setNewProjectName] = useState("My Virector Project");

  const referenceTags = useMemo(
    () => references.map((_, index) => `@image${index + 1}`),
    [references],
  );

  useEffect(() => {
    fetch("/api/health")
      .then((response) => {
        if (!response.ok) throw new Error("Backend unavailable");
        return response.json();
      })
      .then(() => setBackendOnline(true))
      .catch(() => setBackendOnline(false));
  }, []);

  useEffect(() => {
    if (!supabase) {
      setSession(null);
      return;
    }
    supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (!supabase || !session) {
      setProjects([]);
      setSelectedProjectId("");
      return;
    }
    let active = true;
    supabase
      .from("projects")
      .select("id, name")
      .order("created_at", { ascending: true })
      .then(({ data, error }) => {
        if (!active) return;
        if (error) {
          setAuthMessage(error.message);
          return;
        }
        const nextProjects = (data ?? []) as Project[];
        setProjects(nextProjects);
        setSelectedProjectId((current) =>
          nextProjects.some((project) => project.id === current)
            ? current
            : (nextProjects[0]?.id ?? ""),
        );
      });
    return () => {
      active = false;
    };
  }, [session]);

  async function submitAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!supabase) return;
    setAuthBusy(true);
    setAuthMessage("");
    const result =
      authMode === "sign-in"
        ? await supabase.auth.signInWithPassword({ email, password })
        : await supabase.auth.signUp({ email, password });
    if (result.error) {
      setAuthMessage(result.error.message);
    } else if (authMode === "sign-up" && !result.data.session) {
      setAuthMessage("Check your email to confirm your account, then sign in.");
    } else {
      setAuthMessage(authMode === "sign-in" ? "Signed in." : "Account created.");
    }
    setAuthBusy(false);
  }

  async function createProject() {
    if (!supabase || !session || !newProjectName.trim()) return;
    setAuthBusy(true);
    const { data, error } = await supabase
      .from("projects")
      .insert({ owner_id: session.user.id, name: newProjectName.trim() })
      .select("id, name")
      .single();
    if (error) {
      setAuthMessage(error.message);
    } else {
      const project = data as Project;
      setProjects((current) => [...current, project]);
      setSelectedProjectId(project.id);
      setAuthMessage(`Created ${project.name}.`);
    }
    setAuthBusy(false);
  }

  async function signOut() {
    await supabase?.auth.signOut();
  }

  function addReferences(event: ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.target.files ?? []);
    setReferences((current) => {
      const available = Math.max(0, 9 - current.length);
      return [
        ...current,
        ...selected.slice(0, available).map((file) => ({
          id: crypto.randomUUID(),
          file,
          previewUrl: URL.createObjectURL(file),
        })),
      ];
    });
    event.target.value = "";
  }

  function removeReference(id: string) {
    setReferences((current) => {
      const target = current.find((item) => item.id === id);
      if (target) URL.revokeObjectURL(target.previewUrl);
      return current.filter((item) => item.id !== id);
    });
  }

  function moveReference(index: number, direction: -1 | 1) {
    setReferences((current) => {
      const destination = index + direction;
      if (destination < 0 || destination >= current.length) return current;
      const reordered = [...current];
      [reordered[index], reordered[destination]] = [
        reordered[destination],
        reordered[index],
      ];
      return reordered;
    });
  }

  async function submitRender(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!references.length) {
      setStatus("Upload at least one character or world reference image.");
      return;
    }
    if (supabaseConfigured && (!session || !selectedProjectId)) {
      setStatus("Sign in and select a project before generating video.");
      return;
    }
    const mentioned = new Set(
      Array.from(prompt.matchAll(/@image(\d+)\b/gi), (match) => Number(match[1])),
    );
    const missing = referenceTags.filter((_, index) => !mentioned.has(index + 1));
    if (missing.length) {
      setStatus(`Mention every uploaded image in the prompt: ${missing.join(", ")}.`);
      return;
    }

    setIsRendering(true);
    if (videoUrl?.startsWith("blob:")) URL.revokeObjectURL(videoUrl);
    setVideoUrl(null);
    setStatus("Submitting the directed shot to Virector…");

    const form = new FormData();
    references.forEach(({ file }) => form.append("reference_images", file));
    form.append("direction_prompt", prompt);
    form.append("title", title);
    form.append("video_model", "ltx-video-2b-distilled");
    form.append("aspect_ratio", aspectRatio);
    form.append("output_resolution", resolution);
    form.append("duration_seconds", String(duration));
    form.append("seed", String(seed));
    if (selectedProjectId) form.append("project_id", selectedProjectId);

    try {
      const accessToken = session?.access_token;
      const headers = accessToken
        ? { Authorization: `Bearer ${accessToken}` }
        : undefined;
      const response = await fetch("/api/renders", {
        method: "POST",
        headers,
        body: form,
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "The render request was rejected.");
      }

      const render = payload as RenderResponse;
      setStatus(`${render.message || render.status} Job: ${render.job_id}`);
      if (render.video_url) {
        if (accessToken) {
          const videoResponse = await fetch(render.video_url, { headers });
          if (!videoResponse.ok) throw new Error("The rendered video could not be loaded.");
          setVideoUrl(URL.createObjectURL(await videoResponse.blob()));
        } else {
          setVideoUrl(render.video_url);
        }
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Render request failed.");
    } finally {
      setIsRendering(false);
    }
  }

  const renderDisabled =
    isRendering || (supabaseConfigured && (!session || !selectedProjectId));

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Virector home">
          <span className="brand-mark">V</span>
          <span>Virector</span>
        </a>
        <div className="topbar-meta">
          {session && <span className="user-email">{session.user.email}</span>}
          {session && supabase && (
            <button className="text-button" type="button" onClick={signOut}>
              Sign out
            </button>
          )}
          <span className={`health ${backendOnline ? "online" : ""}`}>
            <span className="health-dot" />
            {backendOnline === null
              ? "Checking engine"
              : backendOnline
                ? "Director online"
                : "Backend offline"}
          </span>
        </div>
      </header>

      <section className="hero" id="top">
        <div>
          <p className="eyebrow">AI video direction workspace</p>
          <h1>Direct the scene.<br />Keep the world consistent.</h1>
        </div>
        <p className="hero-copy">
          Upload the visual truth, name it in one prompt, and send a precise shot
          to the Virector generation pipeline.
        </p>
      </section>

      {supabaseConfigured && !session && session !== undefined && (
        <section className="panel auth-panel">
          <div>
            <p className="eyebrow">Secure workspace</p>
            <h2>{authMode === "sign-in" ? "Sign in to Virector" : "Create your director account"}</h2>
            <p>Your projects and render jobs stay scoped to your account.</p>
          </div>
          <form className="auth-form" onSubmit={submitAuth}>
            <input type="email" placeholder="Email" value={email} onChange={(event) => setEmail(event.target.value)} required />
            <input type="password" placeholder="Password" minLength={6} value={password} onChange={(event) => setPassword(event.target.value)} required />
            <button className="render-button" type="submit" disabled={authBusy}>
              {authBusy ? "Please wait…" : authMode === "sign-in" ? "Sign in" : "Create account"}
            </button>
            <button className="text-button" type="button" onClick={() => setAuthMode(authMode === "sign-in" ? "sign-up" : "sign-in")}>
              {authMode === "sign-in" ? "Need an account? Sign up" : "Already have an account? Sign in"}
            </button>
            {authMessage && <p className="auth-message">{authMessage}</p>}
          </form>
        </section>
      )}

      {session && (
        <section className="project-bar panel">
          <label>
            <span>Active project</span>
            <select value={selectedProjectId} onChange={(event) => setSelectedProjectId(event.target.value)}>
              <option value="">Select a project</option>
              {projects.map((project) => <option value={project.id} key={project.id}>{project.name}</option>)}
            </select>
          </label>
          <div className="new-project">
            <input value={newProjectName} maxLength={160} onChange={(event) => setNewProjectName(event.target.value)} placeholder="New project name" />
            <button type="button" onClick={createProject} disabled={authBusy || !newProjectName.trim()}>Create project</button>
          </div>
          {authMessage && <span className="auth-message">{authMessage}</span>}
        </section>
      )}

      <form className="workspace" onSubmit={submitRender}>
        <section className="panel direction-panel">
          <div className="panel-heading">
            <div><span className="step">01</span><h2>Omni references</h2></div>
            <span className="counter">{references.length}/9</span>
          </div>
          <label className="dropzone">
            <input type="file" accept="image/*" multiple onChange={addReferences} disabled={references.length >= 9} />
            <span className="drop-icon">＋</span>
            <strong>Add character and world images</strong>
            <span>PNG, JPG or WEBP · upload order creates @image tags</span>
          </label>
          {references.length > 0 && (
            <div className="reference-grid">
              {references.map((reference, index) => (
                <article className="reference-card" key={reference.id}>
                  <img src={reference.previewUrl} alt={`@image${index + 1}`} />
                  <div className="reference-overlay"><strong>@image{index + 1}</strong><span>{fileSize(reference.file.size)}</span></div>
                  <div className="reference-actions">
                    <button type="button" onClick={() => moveReference(index, -1)} disabled={index === 0} aria-label={`Move @image${index + 1} earlier`}>←</button>
                    <button type="button" onClick={() => moveReference(index, 1)} disabled={index === references.length - 1} aria-label={`Move @image${index + 1} later`}>→</button>
                    <button type="button" onClick={() => removeReference(reference.id)} aria-label={`Remove @image${index + 1}`}>×</button>
                  </div>
                </article>
              ))}
            </div>
          )}
          <div className="prompt-block">
            <div className="panel-heading compact"><div><span className="step">02</span><h2>Direction prompt</h2></div></div>
            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="@image1 is the lead character and @image2 is the world design. Show @image1 moving naturally through @image2 while preserving identity, wardrobe and environmental details. Describe the camera, lighting, action and timing." maxLength={4000} required />
            <div className="prompt-footer">
              <span>{referenceTags.length ? `Use ${referenceTags.join(", ")}` : "Upload images to create prompt tags"}</span>
              <span>{prompt.length}/4000</span>
            </div>
          </div>
        </section>

        <aside className="panel output-panel">
          <div className="panel-heading"><div><span className="step">03</span><h2>Shot output</h2></div></div>
          <div className={`video-stage ${videoUrl ? "has-video" : ""}`}>
            {videoUrl ? <video src={videoUrl} controls playsInline /> : (
              <div className="video-empty"><span className="play-mark">▶</span><strong>Your directed video will appear here</strong><span>No separate start-frame result</span></div>
            )}
          </div>
          <div className="settings-grid">
            <label className="wide"><span>Shot title</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
            <label><span>Aspect ratio</span><select value={aspectRatio} onChange={(event) => setAspectRatio(event.target.value)}><option>9:16</option><option>16:9</option><option>4:5</option><option>1:1</option></select></label>
            <label><span>Resolution</span><select value={resolution} onChange={(event) => setResolution(event.target.value)}><option>Preview</option><option>720p</option><option>1080p</option></select></label>
            <label className="wide range-field"><span>Duration <strong>{duration}s</strong></span><input type="range" min="1" max="15" value={duration} onChange={(event) => setDuration(Number(event.target.value))} /><div><span>1s</span><span>15s</span></div></label>
            <label className="wide"><span>Continuity seed</span><input type="number" min="0" value={seed} onChange={(event) => setSeed(Number(event.target.value))} /></label>
          </div>
          <button className="render-button" type="submit" disabled={renderDisabled}>{isRendering ? "Directing shot…" : "Generate video"}<span>↗</span></button>
          <div className="status-box" aria-live="polite"><span className="status-label">Render status</span><p>{status}</p></div>
        </aside>
      </form>
    </main>
  );
}
