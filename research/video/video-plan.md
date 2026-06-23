# Plan: Create Introductory Course Video for Vooglaadija Project

## Objective

Create a short (5-8 minute) introductory video showcasing the Vooglaadija YouTube Link Processor project as the final epic task for the "Junior to Senior Developer" TalTech course.

---

## 1. Video Concept & Goals

**Target Audience:** Course instructors and assessors evaluating final projects.

**Purpose:** Demonstrate technical competence, project completeness, and readiness for senior-level work through a polished video presentation.

**Video Style:** Professional yet approachable - technical demo with narrative voiceover.

---

## 2. Pre-Production Phase

### 2.1 Script & Storyboard

| Scene | Duration | Content |
|-------|----------|---------|
| **Cold Open** | 0:15 | Quick visual hook - animated logo or demo of download completing |
| **Introduction** | 0:30 | Project name, team (if applicable), brief tagline |
| **The Problem** | 0:45 | Why this project exists - extracting YouTube media is tedious |
| **Architecture Overview** | 1:00 | High-level diagram showing client → API → Redis → Worker → Storage |
| **Demo: Web UI** | 1:30 | Register → Login → Create download → Real-time status |
| **Demo: API** | 1:00 | Swagger UI or curl commands showing REST API |
| **Architecture Deep Dive** | 1:00 | Key technical decisions: async, JWT auth, outbox pattern, HTMX |
| **Tech Stack** | 0:30 | Quick showcase of technologies used |
| **CI/CD & Deployment** | 0:30 | Docker, GitHub Actions workflows |
| **Closing** | 0:30 | Key achievements, lessons learned, what's next |

### 2.2 Recording Setup Checklist

- [ ] Screen recording software (OBS Studio recommended - free)
- [ ] Microphone for voiceover quality
- [ ] Clean desktop background
- [ ] Browser with demo data prepared
- [ ] Terminal with colored prompts ready
- [ ] Swagger UI accessible at localhost:8000/docs

### 2.3 Content Outline

#### Scene 1: Cold Open (0:15)

```text
"Imagine downloading any YouTube video with just a link..."
[Show: Paste URL → Click download → File ready]
```

#### Scene 2: Introduction (0:30)

```text
"Hi, I'm [Name]. Today I'll be presenting Vooglaadija - a production-grade 
YouTube media extraction API built as part of the Junior to Senior Developer 
program at TalTech."
[Show: Project title card with logo]
```

#### Scene 3: The Problem (0:45)

```text
"Manually downloading YouTube videos is cumbersome. You need browser 
extensions, special software, or complex command-line tools. Vooglaadija 
provides a clean REST API and web interface that anyone can use."
[Show: Brief comparison - complex vs simple]
```

#### Scene 4: Architecture Overview (1:00)

```text
"At its core, Vooglaadija follows a producer-consumer pattern. The FastAPI 
server acts as the API and Web UI. When a download is requested, a job is 
queued in Redis. A separate worker process consumes jobs and uses yt-dlp 
to extract the media. Results are stored and served back to the client."
[Show: Animated architecture diagram]
```

#### Scene 5: Demo - Web UI (1:30)

```text
"Let's see it in action. First, I'll register a new user..."
[Screen record: Register → Login → Dashboard → Create download]
[Highlight: Real-time status updates via HTMX SSE]
```

#### Scene 6: Demo - API (1:00)

```text
"For developers, we provide a complete REST API with JWT authentication..."
[Screen record: Swagger UI or terminal curl commands]
```

#### Scene 7: Architecture Deep Dive (1:00)

```text
"Key technical highlights include: [CHOOSE 3-4 BASED ON STRENGTHS]
- Async/await throughout for high throughput
- JWT authentication with access/refresh token rotation  
- The outbox pattern for reliable job queuing
- HTMX for dynamic web interactions without JavaScript frameworks
- Comprehensive CI/CD with GitHub Actions"
[Show relevant code snippets or architecture details]
```

#### Scene 8: Tech Stack (0:30)

```text
"Built with Python 3.12, FastAPI, PostgreSQL, Redis, and containerized with 
Docker. The frontend uses HTMX and Tailwind CSS for a modern experience 
without the complexity of a JavaScript framework."
[Show: Tech badges or icons]
```

#### Scene 9: CI/CD & Deployment (0:30)

```text
"The project includes comprehensive CI/CD pipelines with Docker builds, 
integration tests, and deployment workflows for development and staging 
environments."
[Show: GitHub Actions workflow running]
```

#### Scene 10: Closing (0:30)

```text
"Vooglaadija demonstrates key skills for a senior developer: system design, 
async programming, security patterns, and DevOps practices. Thank you for 
watching!"
[Show: Final title card with links]
```

---

## 3. Production Phase

### 3.1 Recording Steps

1. **Prepare Environment**

   ```bash
   # Start the application
   docker-compose up -d

   # Or for local development
   hatch run dev
   docker-compose up -d redis db

   # Verify all services running
   curl http://localhost:8000/api/v1/health
   ```

1. **Create Demo Data**
  - Register a test user
  - Create 2-3 sample downloads (one pending, one completed, one failed)
  - Prepare a real YouTube URL for live demo

1. **Record Screen Sections**
  - Record architecture diagram animation (can use simpler tools)
  - Record web UI demo flow
  - Record API demo (Swagger UI)
  - Record terminal shots of docker-compose, logs, etc.

1. **Voiceover Recording**
  - Record after visuals are prepared
  - Use consistent microphone distance
  - Minimize background noise

### 3.2 Recording Tips

- **Resolution:** 1920x1080 minimum
- **Frame Rate:** 30fps for screen, can use 60fps for animations
- **Audio:** Record in quiet space, aim for -20dB peak level
- **Chrome:** Use incognito mode to avoid cached extensions

---

## 4. Post-Production Phase

### 4.1 Editing Software Options

| Software | Cost | Complexity |
|----------|------|------------|
| DaVinci Resolve | Free | Medium |
| OBS Studio | Free | Low (record + basic edit) |
| CapCut | Free | Low |
| Adobe Premiere | Subscription | High |

### 4.2 Editing Checklist

- [ ] Cut and trim recorded segments
- [ ] Add transitions between scenes (subtle fades)
- [ ] Add voiceover audio
- [ ] Add background music (copyright-free from Pixabay, Uppbeat)
- [ ] Add text overlays for key points
- [ ] Add cursor highlights or click indicators
- [ ] Export in web-friendly format (MP4, H.264, <500MB for 5min)

### 4.3 Quality Checklist

- [ ] Audio levels consistent (-20dB target)
- [ ] No background hum or noise
- [ ] Video and audio synchronized
- [ ] Transitions are smooth
- [ ] Text is readable (minimum 24pt for subtitles)
- [ ] Branding/logo visible at intro/outro

---

## 5. Final Output Specifications

| Property | Value |
|----------|-------|
| Duration | 5-8 minutes |
| Resolution | 1920x1080 (or 1280x720 minimum) |
| Format | MP4 (H.264 codec) |
| File Size | <500MB preferred |
| Aspect Ratio | 16:9 |
| Audio | AAC, 48kHz, stereo |
| Frame Rate | 30fps |

---

## 6. Timeline

| Phase | Time Estimate |
|-------|---------------|
| Script & Storyboard | 2-3 hours |
| Environment Setup | 1-2 hours |
| Recording | 1-2 hours |
| Post-Production | 3-4 hours |
| Review & Revisions | 1-2 hours |
| **Total** | **8-14 hours |

---

## 7. Deliverables

1. **Primary:** Final edited video file (MP4)
1. **Supporting:** Script document (for reference)
1. **Optional:** Behind-the-scenes recording of demo takes

---

## 8. Key Selling Points to Highlight

Based on the course requirements and project analysis, emphasize:

1. **Async Architecture** - Showcases Python expertise
1. **Full-Stack Implementation** - API + Web UI + Worker
1. **Production Patterns** - Outbox pattern, graceful shutdown
1. **Security** - JWT auth, CSRF protection
1. **DevOps** - Docker, CI/CD, testing
1. **Clean Code** - Type hints, linting, testing

This demonstrates readiness for senior-level responsibilities as required by the course.
