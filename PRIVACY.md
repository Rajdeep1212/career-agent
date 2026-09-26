# Privacy Policy

Last updated: 26 September 2026

Career Agent is a local-first job-search app. It runs on your own computer,
and the person running it is its only user. There is no Career Agent server,
account or cloud service, and the developer never receives your data.

## What the app stores

Everything the app stores stays on your computer, in the `data/` folder of
your copy of the app:

- your CV and the profile parsed from it
- your job-search preferences, saved jobs and application tracker
- email drafts you write or approve
- job-search history and request counts per provider
- sign-in tokens for Gmail or LinkedIn, if you connect them, encrypted with a
  key kept in your own `.env` file

You can delete any of this at any time by deleting files in `data/`.
Disconnecting Gmail or LinkedIn in the app removes the stored token.

## Gmail

If you connect Gmail, the app asks for one permission only: **send email**
(`gmail.send`). It cannot read, search, delete or change your mailbox.

The app sends an email only after you review the draft, approve it, and press
Send. It never sends anything automatically.

Career Agent's use and transfer of information received from Google APIs
adheres to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements. Gmail data is used only to send the
emails you approve, is not used for advertising, is not sold, and is not
shared with anyone.

## What leaves your computer

Career Agent does not sell, rent or share your personal data with third
parties. It contacts outside services only when you use a feature that needs
them:

- **Job searches** send your search terms (for example, a role and a city) to
  the job providers you configure: JSearch, Adzuna or Jooble. Your CV and
  profile are not sent.
- **Approved emails** are sent through Gmail to the recipient you chose.
- **Checking whether a job is still open** loads that job's public web page.
- **LinkedIn**, if you connect it, shares your basic profile (name, email,
  photo) with the app on your computer.

The optional chat assistant runs on your own computer (Ollama). No hosted AI
model receives your CV or profile.

Each of these services has its own privacy policy.

## Contact

Questions about this policy: r.mahanty2003@gmail.com
