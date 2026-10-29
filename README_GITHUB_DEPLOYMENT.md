# FUNAAB ONE — GitHub/Vercel preparation

## Important status
This is a deployment-preparation copy of the project ZIP supplied for review. It does not include the local SQLite database or uploaded files. Keep your original project and database backed up. Do not upload private database files or credentials to GitHub.

## Before deployment
1. Create a private GitHub repository and upload this folder's code.
2. In Vercel, import that repository.
3. Add environment variables in Vercel Project Settings → Environment Variables:
   - `SECRET_KEY`: a long random secret
   - `DATABASE_URL`: Supabase PostgreSQL **transaction pooler** connection URI, with the actual password URL-encoded if needed
   - `CLOUDINARY_CLOUD_NAME`
   - `CLOUDINARY_API_KEY`
   - `CLOUDINARY_API_SECRET`
4. Deploy only after the Supabase schema/data migration plan is ready.

## Database warning
The database file inside the supplied ZIP was zero bytes. No existing accounts, posts, messages, or other rows can be migrated from that file. The code package intentionally excludes `instance/`. We need a valid export of your actual local database to preserve existing data. Do not run a migration against the empty ZIP database.

## Media warning
The code can store new profile pictures, marketplace images, and processed post media in Cloudinary when all three Cloudinary environment variables are set. The existing post-upload flow still receives files through Flask; Vercel imposes a small request-body limit on serverless functions, so large videos need a direct browser-to-Cloudinary signed upload flow before production use. Do not assume the current 1 GB local video limit works on Vercel.

## Existing local development
When `DATABASE_URL` is unset, the app continues to use its SQLite database locally. Keep your original `instance/funaab_one.db` outside this deployment copy. Install dependencies with `python -m pip install -r requirements.txt`.

## Security
Never commit `.env`, API secrets, the local database, or uploaded student files. If a secret has been exposed, rotate it in the provider dashboard.

## Spotify music integration

FUNAAB ONE's `/music` page uses Spotify's Web API for public track search and Spotify's official embedded player for playback. Configure these Vercel Environment Variables (Production and Preview as needed):

- `SPOTIFY_CLIENT_ID`: the app Client ID from Spotify for Developers
- `SPOTIFY_CLIENT_SECRET`: the app Client Secret from Spotify for Developers (Secret type; server-side only)

Redeploy after setting or changing environment variables. Do not expose the Client Secret in templates, browser JavaScript, screenshots, or GitHub. This initial integration uses Spotify Client Credentials for catalogue search; it does not log each student into Spotify or grant personalized playback. Spotify controls playback availability, account requirements, app access, and policy restrictions. Spotify Development Mode currently has user and access limitations, so verify the current rules before rolling this out to the whole campus. Favourites are stored in local storage on each browser/device. No database migration is required.


## FUNAAB AI (Groq) setup

FUNAAB AI uses the Groq OpenAI-compatible chat-completions API. The API key is read only on the server from `GROQ_API_KEY`; never place it in browser JavaScript or commit it to GitHub.

### Local development
1. Copy `.env.example` to `.env`.
2. Set `SECRET_KEY` to a long random value.
3. Set `GROQ_API_KEY` to the key created in your Groq console.
4. Optionally set `GROQ_MODEL` to a model currently available to your Groq account.
5. Install dependencies and run the app as usual.
6. Sign in and open `/ai`.

### Vercel
In Project Settings → Environment Variables, add `GROQ_API_KEY` and `GROQ_MODEL` (optional, default: `llama-3.3-70b-versatile`). Ensure `SECRET_KEY` and `DATABASE_URL` are already configured, then redeploy. New `ai_chat_session` and `ai_chat_message` tables are created by the app's existing `db.create_all()` startup path; for production, a versioned migration is preferable as the application grows.

Groq free-tier quotas and model availability can change. If the selected model becomes unavailable, set `GROQ_MODEL` to a supported model from your Groq console. Never share your API key in screenshots or chat messages.
