This is a comprehensive, step-by-step technical and organizational summary of the "Senior Developer" (Vanemarendaja) final demo session held on May 4-5, 2026.

---

### **Overview**

The document is a transcript of a series of final project presentations by various development teams. The presentations cover a wide range of web and mobile applications, focusing on problem-solving, technology stacks, team collaboration, and minimum viable products (MVPs). The session is moderated by "Speaker 1," who facilitates transitions and concludes the course.

---

### **Detailed Project Summaries**

#### **1. Team 19: "Opi" (Childcare Communication App)**

* **Purpose:** A modern communication platform for parents and kindergartens to replace fragmented tools like Facebook groups, emails, and paper notices.
* **Functionality:** Teacher-created announcements, child profile management, direct/group messaging, a shared calendar for events/absences, and an image gallery for daily activities.
* **Tech Stack:** JavaScript monorepo; Frontend: Next.js 16, React 18, Tailwind CSS, Shadcn UI; Backend: Go; Database: PostgreSQL; Auth: Google OAuth 2.0 with secure HTTP-only cookies.
* **DevOps/QA:** Containerized with Docker Compose (one command setup), automated migrations, seeding of test data, and unit/E2E tests running in GitHub Actions.

#### **2. Team 15: "Pickleball Tournament Manager"**

* **Purpose:** To solve the lack of real-time data and rankings in small-scale sports tournaments.
* **Functionality:** Real-time score tracking, tournament brackets, player rankings, and schedule management accessible to organizers, players, and spectators.
* **Tech Stack:** Backend: Laravel 13 (REST API); Frontend: Vue 3; Database: PostgreSQL; Real-time updates via WebSockets.
* **Workflow:** Roles were fixed throughout the project; management via Jira; hybrid meetings (online and physical).

#### **3. Team 17: "Auto Päevik" (Car Diary)**

* **Purpose:** Consolidating vehicle history to increase trust between owners and buyers.
* **Functionality:** Storage for repair/maintenance documents, inspection reminders, and insurance tracking. Owners can transfer history to new owners. Includes a public search feature by license plate.
* **Business Value:** Useful for repair shops to upload verified documents directly.
* **Demo:** Showed account creation, car profile management (public vs. private visibility), and the "last documents" view.

#### **4. Team 3: "Kapoti Abi" (Automotive Repair AI)**

* **Purpose:** Streamlining the process of getting repair quotes for vehicles.
* **Functionality:** Instead of calling multiple shops, users describe the problem via a short chat. AI analyzes the issue and generates a professional report for mechanics.
* **Tech Stack:** AI integration (LLMs) to make messages machine-readable and sort shop responses.
* **Development:** 98% complete; heavy focus on automated integration and unit testing (approx. 90% coverage). Aiming for a full market launch next year.

#### **5. Unnamed Team: "Bar/Club Ordering System"**

* **Purpose:** Eliminating queues in busy night clubs and bars to increase revenue.
* **Functionality:** Customers order and pay via phone. The system uses data-driven insights to send personalized notifications/offers to bring customers back.
* **Business Model:** Monthly subscription fee for the venue plus a small percentage of each transaction.

#### **6. Team 2: "UX Hell"**

* **Purpose:** A simulator/game showcasing the impact of bad user experience.
* **Functionality:** Users must complete simple tasks (like creating an account) while the system intentionally acts counter-intuitively. Results are compared against others.
* **Tech Stack:** Frontend: Next.js; Backend: Java Spring Boot; Auth: OAuth 2.0; Database: PostgreSQL; Monitoring: Prometheus and Grafana.
* **Methodology:** Weekly rotating "Retro" leads and CI/CD pipelines with automated tests.

#### **7. Team 50: "Hernes" (Gardening App)**

* **Purpose:** A mobile diary for gardeners to track planting, animal care, and garden maintenance.
* **Functionality:** Multi-garden management, image galleries for plots, logging for plants, animals (bees, poultry), and composting.
* **Tech Stack:** Backend: Node.js; Frontend: React Native/Expo (targeting Android, iOS, and Web); Auth: Firebase; Database: Supabase.
* **User Interface:** Specifically designed to be available in Estonian to lower the barrier for local hobbyists.

#### **8. Team 21: "Stop" (Sports Activity Network)**

* **Purpose:** A map-based social platform to find workout partners or join local sporting events (e.g., swimming at Linnahall or disc golf).
* **Functionality:** Organizers can pin events to a map; users see local activities based on proximity.
* **Tech Stack:** Frontend: React; Backend: .NET ASP.NET; Database: PostgreSQL.
* **DevOps:** Full CI/CD via GitHub Actions; deployment using Docker Compose. Used "Aspire" for container orchestration and development.

#### **9. Team 13: "Interactive Greeting Cards"**

* **Purpose:** A platform for creating and sending digital greeting cards, invitations, or promo messages via QR codes or links.
* **Functionality:** Users can edit text, colors, and images. Includes a "lock" feature where a card only opens at a specific time.
* **Tech Stack:** Deployed on university servers. Focus on security and potential future CRM integration for businesses to send promotional materials.

#### **10. Team 33: "Stiilihaab" (Clothing Search Aggregator)**

* **Purpose:** A unified search engine for all Estonian clothing e-shops to avoid comparing multiple tabs.
* **Functionality:** Over 30 categories (men, women, kids, workwear, etc.). Users can filter by brand, price, or novelty.
* **Tech Stack:** A "24/7 dynamic scraper network" that crawls Estonian shops responsibly (never "bombarding" servers, capping requests to 25 items at a time). Data is normalized for comparison.

#### **11. Team 35: "Prediction Market Simulator"**

* **Purpose:** A simplified crypto price direction prediction tool.
* **Functionality:** 5-minute cycles where users bet on whether Bitcoin (BTC) goes up or down.
* **Tech Stack:** Real-time data from CoinGecko API.
* **Future Plans:** Moving from a simulator to real payments, implementing WebSockets for real-time updates, and adding stronger financial-grade security.

#### **12. Unnamed Team: "The Pole" (Quiz Platform)**

* **Purpose:** A Kahoot-like interactive quiz game with a focus on ease of use.
* **Functionality:** 2-click registration, free image/music support, and randomized answer orders to prevent cheating.
* **Tech Stack:** Real-time interaction tracking (detecting user disconnects).

#### **13. Team 11: "Ruum Booking" (Office Room Reservation)**

* **Purpose:** Automating shared meeting room bookings for offices to replace Excel/Email.
* **Functionality:** 24/7 self-service. Users get a PIN code upon payment to access the room.
* **Tech Stack:** Backend: Java Spring Boot; Frontend: TypeScript/Vue; Database: PostgreSQL; Integrations: Google Calendar API, Stripe (payments), Cloudinary (images).
* **Management:** Weekly sprints, Kanban boards, and rotating project manager roles.

#### **14. Team 8: "Droonidest.ee" (Drone Education)**

* **Purpose:** An educational platform explaining the technology and defense against military drones (specifically referencing the war in Ukraine).
* **Functionality:** Interactive "Drone Assembly" game to learn components, a gallery of different drone types, and a blog/campaign section.
* **Tech Stack:** Java Spring Boot, PostgreSQL, Grafana, Docker.
* **AI Integration:** Used AI agents (Claude, Cursor) for "Spec-Driven Development."

#### **15. Team 27: "Pasiga Pais" (Tallinn Bicycle Planner)**

* **Purpose:** A routing app specifically for cyclists in Tallinn, accounting for local quirks (high curbs, tram tracks, park shortcuts).
* **Functionality:** Option to choose "Fastest" vs. "Safest" routes. Includes automatic re-routing if the cyclist goes off path.
* **Tech Stack:** Java Spring Boot, React, Vite. Uses "pgRouting" on OpenStreetMap (OSM) data.
* **Methodology:** Termed their work style "Semi-Hackathon," finding it most effective for the complex routing logic.

#### **16. Team 1: "KlassAR" (Glossary/Dictionary)**

* **Purpose:** An AI-enhanced language learning dictionary to help users remember new words through repetition.
* **Functionality:** Users add words; AI (GPT-based) provides definitions if the user doesn't provide one. Includes a "Quiz" mode based on the user's past mistakes.
* **Tech Stack:** Java Spring Boot, PostgreSQL. Deployed using GitHub Container Registry and a reverse proxy on the school server.

#### **17. Unnamed Team: "Enterprise AI Trust"**

* **Purpose:** A platform to cryptographically verify AI responses and actions to comply with regulations (like the EU AI Act).
* **Functionality:** Creating audit logs and digital signatures for AI-generated outputs to ensure accountability in high-risk sectors (Finance, Medical).
* **Business Case:** Aimed at enterprises needing to prove their AI models are behaving according to legal standards.

---

### **General Technical Observations**

Throughout the session, several recurring technologies and methodologies were highlighted:

* **Backends:** Heavy preference for **Java Spring Boot** and **PostgreSQL**.
* **Frontends:** **React** and **Next.js** were the dominant frameworks, followed by **Vue**.
* **Containerization:** Almost every team used **Docker** and **Docker Compose** for deployment.
* **AI:** Multiple teams integrated LLMs (OpenAI, Claude) not just as features (definitions, chat), but as development tools (writing tests, generating code via Cursor).
* **Testing:** High emphasis on automated testing (Unit, Integration, E2E) and CI/CD pipelines via GitHub Actions.

---

### **Closing Remarks by the Instructor (Speaker 1)**

* **Evaluation:** A feedback and voting session followed the presentations to identify the most impressive projects (notable mentions included *Kapoti Abi*, *UX Hell*, and *Droonidest.ee*).
* **Course Wrap-up:** The instructor noted the high quality of work, given the "Junior-to-Senior" jump required.
* **Future:** The course materials and servers will remain open for some time (at least until the end of the year) for students to migrate their projects.
* **Networking:** Students were encouraged to use the 150-person group to share job listings or find future colleagues.
* **Final Tasks:** Students must provide feedback on teammates and the course itself to help improve the next iteration of the program.
