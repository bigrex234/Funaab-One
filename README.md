# FUNAAB ONE 🌿

### One Campus. One Community. One Platform.

**FUNAAB ONE** is a student-focused social networking and campus community platform built to connect students of the Federal University of Agriculture, Abeokuta (FUNAAB).

The platform brings social networking, student communities, private messaging, a student marketplace, and AI-powered assistance into one place.

🌐 **Live Website:** https://funaab-one.vercel.app

---

## ✨ Features

### 🏠 Social Feed
- Create and share posts with other students.
- Upload photos and videos.
- Like, comment on, and repost content.
- View posts from the campus community.
- Preserve media aspect ratios for a better viewing experience.

### 👤 User Profiles
- Create and customize your profile.
- Upload profile pictures.
- Follow and unfollow other students.
- View followers, following, and posts.
- Edit profile information.

### 💬 Messaging
- Private messaging between users.
- Group conversations.
- Message notifications and unread badges.

### 👥 Communities
- Create and join student communities.
- Participate in discussions.
- Share ideas and interact with other members.

### 🛍️ Student Marketplace
- Buy and sell items within the student community.
- Discover products listed by other students.
- Search for available items.
- Contact sellers directly.

### 🔔 Notifications
- Notifications for likes, comments, replies, and follows.
- Message notifications.
- Centralized notification experience.

### 🤖 FUNAAB AI
- AI-powered conversational assistant.
- Integrated with the Groq API.
- Designed to provide users with an interactive AI experience.

### 🎵 Music Integration
- Music discovery and playback features through Spotify integration.

---

## 🛠️ Technology Stack

| Technology | Purpose |
|---|---|
| Python | Backend programming |
| Flask | Web application framework |
| Flask-SQLAlchemy | Database integration |
| PostgreSQL / Supabase | Database and backend storage |
| HTML5 | Page structure |
| CSS3 | Styling and responsive layouts |
| JavaScript | Frontend interactions |
| Cloudinary | Image and video storage |
| Groq API | AI assistant |
| Spotify API | Music integration |
| Vercel | Deployment and hosting |
| Git & GitHub | Version control |

---

## 🏗️ Project Architecture

FUNAAB ONE uses a Flask-based backend to handle application logic, user interactions, and database operations.

The application connects to external services for media management, AI functionality, and music integration.

**General architecture:**

```text
                 FUNAAB ONE
                      |
              Flask Application
                      |
          +-----------+-----------+
          |           |           |
       Database    Cloudinary   External APIs
          |           |           |
       Supabase     Media      Groq / Spotify
                      |
                Vercel Hosting
```

---

## 🚀 Getting Started

Follow these steps to run FUNAAB ONE locally.

### 1. Clone the repository

```bash
git clone https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
```

### 2. Enter the project directory

```bash
cd YOUR-REPOSITORY
```

### 3. Create a virtual environment

```bash
python -m venv venv
```

Activate it:

**macOS / Linux**

```bash
source venv/bin/activate
```

**Windows**

```bash
venv\Scripts\activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Configure environment variables

Create a `.env` file in your project root.

Example:

```env
SECRET_KEY=your_secret_key
DATABASE_URL=your_database_url

CLOUDINARY_CLOUD_NAME=your_cloudinary_cloud_name
CLOUDINARY_API_KEY=your_cloudinary_api_key
CLOUDINARY_API_SECRET=your_cloudinary_api_secret

GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=openai/gpt-oss-120b

SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
SPOTIFY_REDIRECT_URI=your_spotify_redirect_uri
```

Add any other environment variables required by your application.

**Important:** Never upload your `.env` file, API keys, database credentials, or other secrets to GitHub.

### 6. Run the application

```bash
python app.py
```

Open your browser:

```text
http://127.0.0.1:5000
```

---

## ☁️ Deployment

FUNAAB ONE is hosted on Vercel.

General deployment steps:

1. Push your project to GitHub.
2. Import the repository into Vercel.
3. Configure the required environment variables in Vercel.
4. Confirm your database and external services are configured.
5. Deploy the application.
6. Visit your production URL.

---

## 🔐 Security

FUNAAB ONE uses security-conscious development practices, including:

- Password hashing.
- Environment-based configuration.
- CSRF protection.
- API credentials stored outside source code.
- Database-backed user information.

Security measures should be reviewed and strengthened as the platform grows.

---

## 🎯 Project Vision

FUNAAB ONE aims to create a connected digital campus where students can:

- Connect with fellow students.
- Share information and ideas.
- Build communities.
- Buy and sell useful items.
- Communicate privately.
- Discover music and use AI-powered tools.

The long-term vision is to make campus life more connected, accessible, and interactive through technology.

---

## 👨‍💻 Developer

**Created by Rex**

Software Engineering  
Federal University of Agriculture, Abeokuta (FUNAAB)

FUNAAB ONE is a student-focused technology project developed to improve campus communication, interaction, and digital engagement.

---

## 🤝 Contributions

Contributions, suggestions, and ideas are welcome.

If you would like to contribute:

1. Fork the repository.
2. Create a new feature branch.
3. Make your changes.
4. Submit a pull request.

---

## 📄 License

Choose and add an appropriate open-source license before distributing or accepting external contributions.

---

**FUNAAB ONE — Connecting the FUNAAB Community, One Student at a Time.** 🌿
