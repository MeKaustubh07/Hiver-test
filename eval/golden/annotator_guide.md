# SpotifyCares Intent Annotator Guide (v2)

Label each thread from the **burst**: every consecutive customer tweet posted before the first @SpotifyCares reply. Pick **one** intent and **one** disposition (`auto` = a public self-serve reply is enough; `escalate` = a human must take over, usually via DM).

## 0. Workflow (in order)
1. Read the burst. Note any PII, safety, legal or GDPR content (Section 3, gates).
2. Pick the intent using the definitions and the precedence order (Section 1-2).
3. Walk the disposition rules top-down; the first that fires wins (Section 3).

## 1. Intents (10)

**content_availability** - a named song/album/artist/podcast is missing, removed, region-locked, unplayable on *every* client, or has wrong metadata; a regional editorial playlist is stale; Spotify is not launched in the named country. Default auto.
- "add teen, age by svt please"
- "I can't listen to any Daft Punk song on either spotify web, android app or desktop, any solution?"
- "when are we going to enjoy Spotify 🇮🇳? #stillnotinourcountry"

**billing_and_subscription** - money moved or the paid state is wrong: charges, refunds, paid-but-Free, ads on a *stated* Premium account, promo charged wrongly, checkout/payment-page errors, cancel-to-stop-payments, Family/Student/Hulu/partner *failures*. Default escalate.
- "second month in a row I've been charged for premium but my account is still \"free\""
- "I can not join to a premium for family plan, every time I try to accept an invitation, i got an error"
- "im trying to update my payment info... it keeps saying my CSRF token is invalid"

**account_access_and_settings** - cannot get in on *any* client, cannot create/identify the account, or needs a backstage change (email, country, username at sign-up, Facebook link, merge, marketing opt-out); abroad/14-day/VPN enforcement on an existing account. Default escalate.
- "can't login! Too many redirects error message"
- "I've moved from the UK to Canada and it won't let me change the country I'm in"
- "Your unsubscribe button for marketing emails does not seem to remove me from the list"

**account_security** - an *unknown* third party has/uses the account, attacker changed details, phishing/breach check, deceased user. Default depends.
- "Someone is definitely using my Spotify I have no idea what this music is or why it's in my recently played"
- "My account has been compromised... the e-mail has been changed on my account"
- "received email asking my to follow link to change spotify password. is this real or phishing?"

**app_or_playback_issue** - the app/playback misbehaves on a device or client: crashes, skipping, won't play, downloads or library gone, device-scoped login failure, integration failures, suspected outage. Default depends.
- "my (android) app freezes after it plays ads and I need to restart it every time"
- "why has all of my music undownloaded?"
- "is Spotify down at the moment ? Any search for anything or Home not working"

**feature_request_and_feedback** - asks for a capability that does not exist, complains about a deliberate change/default/design, ads, recommendations, shuffle, curation, pricing policy, or rants about the product with nothing broken. Default auto.
- "should have an alarm feature so I can wake up to my soundtrack"
- "Why isn't there an option to block artists if you don't want them on shuffle... please add an option"
- "So my spotify activity to facebook mysteriously stops... you told someone the feature is no longer supported?"

**how_to_and_product_question** - how to use an existing capability, whether something is possible, what a plan costs/covers, how to subscribe, policy facts (Family sharing, ownership transfer, Yearly switch, NUS/UNiDAYS, regional promo, trial eligibility with no charge), intended behaviour (Autoplay, Suggested Tracks, queue). No failed attempt or charge. Default auto.
- "How do you copy URI on ?"
- "Yo is it possible to transfer \"billing responsibility\" on a family account to another member?"
- "why can't I use my NUS student discount card to get Spotify Premium anymore?"

**support_followup_and_channel_request** - about the support interaction itself: chasing a DM/email, asking to be contacted, phone/channel requests, or a vague complaint with an account/plan/money/login keyword and no symptom. Default escalate.
- "Hi! Sent you a DM."
- "Is there a phone number I can call someone at"
- "having a lot of problems with Spotify premium. Is someone able to assist please?"

**artist_and_creator** - sender is an artist/label/podcaster/partner about *their own* content, profile, stats, reports, submissions or applications ('my release', 'my song', 'our podcast'). Default auto.
- "had a release today... made a new artist page instead of attaching to my verified page!"
- "Hey still waiting since yesterday having the problem with my single \"LOST\" sorted out"
- "Mystreamcount arenot updated for my song... to my aggregater"

**other** - (a) praise/thanks/banter/chatter/shared links; (b) vague, image-only, promo-copy or fragment with no diagnosable words and no keyword; (c) off-topic with a fixed answer (careers, presale codes, follows, recommendations); (d) human-only (GDPR/data export, account deletion, bereavement, legal/press). Default depends.
- "saved the day! ❤️ Thank you!"
- "So frustrated! Can't find my presale code for the concert in NYC !! SOS"
- "Hey ! I need a document with all the datas you have about me, is that possible ?"

## 2. Boundary rules and tie-breaks

- **Precedence when several intents appear:** security > access > billing > app > content > artist > feature > how_to > other. support_followup only when nothing describable is present or the customer explicitly chases ('no one responds', 'still waiting').
- **Capability exists?** Ignore phrasing. Registry says it exists -> how_to; does not exist -> feature_request. (Folders, collaborative playlists exist; explicit filter, block artist, playlist-like notifications, lossless, ad opt-out do not.)
- **Policy fact vs failure:** a plan question with no error and no charge (share Premium with wife, transfer Family ownership, switch to Yearly, NUS card, UNiDAYS login, promo in Quebec, trial not applied and not charged) -> how_to, auto. Any error message, failed invite, or charge -> billing, escalate.
- **Student:** charge or expired-on-live-subscription -> billing; otherwise how_to.
- **Hulu/partner bundle mentioned** -> billing, escalate.
- **Logged in + error on payment page** -> billing. Error at login -> account_access. Login fails on one device only / 'you're offline' / dead login button -> app rung 1.
- **Named artist/title won't play on every client** -> content (canned line + 'what country is your account set to?'), never the device ladder. 'From my library/downloads' with no title -> app.
- **Data loss wording:** download/offline/undownloaded -> downloads shortcut (auto, first single-device occurrence). Songs/playlists/library gone with no download wording -> account-scoped, escalate. Second time / always / new phone / after unexplained logout -> escalate now.
- **Pausing:** 'used somewhere else' or unknown speaker -> account_security (auto first contact). Known co-listener -> how_to. Stops after every song on one device -> app rung 1.
- **Unfamiliar music:** in Recently Played / unknown device -> account_security. Only in Discover Weekly/suggestions -> feature_request.
- **Used-to-work vs deliberate change:** 'where did X go' with no attribution -> app rung 1. Customer says it was removed/deprecated/'is this permanent' -> feature_request.
- **Region:** country not launched -> content. Existing account abroad ('been in the UK 3 months', VPN, 'policy') -> account_access.
- **Ownership:** 'my release/song/podcast/verified page' -> artist; listener report -> content (metadata) or feature (curation). Artist chasing Artist Support stays artist.
- **Vague:** keyword {account, premium, subscription, plan, family, student, money, login, DM, phone} -> support_followup. No keyword -> other. Device or symptom named -> app.
- **Churn/anger** about the product with no symptom -> feature_request (auto). About waiting for support -> support_followup.
- **Tier unknown** ('ads, seriously???', 'Premium not working on my laptop') -> the more likely auto intent, with one clarifying turn.
- **Presale codes** -> other, auto (fixed line). 'Throwing hands' etc. is hyperbole, not a safety trigger.
- **Bare username** in a tweet is not PII for the delete-tweet line; email, phone, card, or username+email is.

## 3. Disposition rules (first match wins)

1. PII (email/phone/card/username+email) -> escalate.
2. Credible self-harm/violence/harassment/bereavement -> escalate (idioms with no target are not triggers; disclaimed jokes -> escalate for review, warm public reply allowed).
3. Legal/press/law enforcement -> escalate.
4. GDPR/data export/account deletion -> escalate (in-app privacy settings are how_to, auto).
5. Spotify breach claim / phishing needing the email -> escalate.
6. Explicit chase, channel/DM request, or vague-with-keyword -> escalate (artist senders -> artist rules).
7. Confirmed incident or known open bug -> auto ('we're on it'). 3+ outage reports in 15 min, unconfirmed -> auto holding line.
8. Security: attacker changed email/password, steps tried, repeat hijack, Premium/paid stated, someone else's account -> escalate. Plain first contact, Free/unstated -> auto (hacked-account page).
9. Money moved / paid state wrong / checkout error / cancel to stop payments -> escalate.
10. Plan-policy fact, no error, no charge -> auto.
11. Plan/entitlement failure (Family, student with charge, Hulu/partner, paid merge, wrong login) -> escalate (blank Family web page: one incognito rung first).
12. Device-scoped login symptom / web-only with 'known issue' wording / reset loop / sign-up spinner -> auto one public rung.
13. Login/identity/settings/abroad on an existing account -> escalate.
14. Artist: chase, royalty/report/partnership -> escalate; first-contact profile/counts/pitching/podcast -> auto redirect.
15. Account-scoped data loss or state (library gone, recurrent downloads loss, 'nothing on any device' stated, duplicate offline devices, ads/needs-Premium on paid) -> escalate.
16. Qualifying steps tried (logout+restart+login, reinstall, other network/device, incognito, 'tried everything') or ladder rungs 2-4 done or unsupported OS -> escalate. Restarting the app does not count. Client crash / car-speaker-console integration stays public.
17. Individual app symptom, first contact -> auto (rung 1 or shortcut).
18. Content, feature, how_to, praise, presale, recommendations, careers -> auto.
19. Tier-ambiguous tie with billing -> auto, one clarifying turn.
20. Tone alone -> never changes disposition.
21. Vague/image-only/fragment -> auto, one clarifying turn.
22. Unsure, tied with different dispositions, non-English, or still unclear after one turn -> escalate.

Never write 'we've replied to your DM', never confirm account status, never promise refunds or features, never echo account details.