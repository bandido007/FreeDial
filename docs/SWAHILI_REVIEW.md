# Swahili labels: review for Tostao

The agent and supervisor pages can now be switched to Swahili (the EN / SW buttons; the choice is
remembered in the browser). Every label is a **draft** until you approve it (UI_DESIGN.md: "Tostao
reviews every Swahili label before release"); the SW button says so when you hover it.

How to change a label: edit `apps/agentdesk/translations/sw.json` (agent page) or
`apps/supervisor/translations/sw.json` (supervisor page). Keep `{words in braces}` exactly as they
are: the page puts numbers and names there. A test fails if one is missing or misspelt.
When you approve the whole list, remove the `_status` line from both files and the hover note on
the SW button.

Not translated on purpose: queue names, wrap-up codes and Not Ready reasons (they come from the
admin pages, as typed there), and the AI's summary (written in English for every agent).

## Word choices to decide first

These words appear on almost every screen. Each has the draft and the alternatives considered.

| English | Draft | Alternatives | Why the draft |
| --- | --- | --- | --- |
| Agent | Mhudumu | Wakala, Mtoa huduma | "Wakala" is used for M-Pesa and insurance agents; "mhudumu" is the person serving the caller |
| Supervisor | Msimamizi | Kiongozi wa timu | Standard in Tanzanian workplaces |
| Ready | Tayari | | |
| Not Ready (my own state) | Sipo tayari | Sijawa tayari, Nimepumzika | First person on the agent's own page |
| Not Ready (another agent) | Hayupo tayari | | Third person on the supervisor page |
| Go Ready / Go Not Ready | Kuwa Tayari / Kuwa Sipo tayari | Niko tayari / Sipo tayari | Button names say the action (UX rule 4) |
| On a call | Kwenye simu | Anaongea na mteja | Short enough for the state bar |
| On hold / Hold / Resume | Imesitishwa / Sitisha / Endelea | Imeshikiliwa, Subirisha | "Kusitisha" is the usual word for pausing a call |
| Wrap-up | Kumalizia | Kukamilisha simu | The UI design draft |
| Wrap-up code | Msimbo wa kumalizia | Aina ya simu | |
| Incoming call | Simu inaingia | Simu mpya | |
| Answer / Decline / Hang up | Pokea / Kataa / Kata simu | Jibu | |
| Transfer / Consult | Hamisha / Shauriana | Peleka | |
| Monitor / Whisper / Barge | Sikiliza / Nong'ona / Ingilia | Fuatilia, Shiriki | |
| Queue | Foleni | Mstari | |
| Caller / Customer | Mpigaji / Mteja | | |
| Notes | Maelezo | Kumbukumbu | |
| Keypad / Digits | Vitufe / Tarakimu | | |
| Mute / Unmute | Zima sauti / Washa sauti | Nyamazisha | |
| Live (updates) | Papo hapo | Moja kwa moja, Mubashara | |
| Service level | Kiwango cha huduma | | |
| AI | AI | Akili bandia | Callers and staff say "AI" |

## Agent page (166 labels)

| English | Swahili draft |
| --- | --- |
| A call is waiting for you | Kuna simu inakusubiri |
| Agent | Mhudumu |
| Agent status | Hali ya mhudumu |
| Agent workspace | Eneo la kazi la mhudumu |
| Answer | Pokea |
| Average talk time | Wastani wa muda wa maongezi |
| Blind transfer | Hamisha moja kwa moja |
| Call ended | Simu imekatika |
| Call notifications are blocked in your browser | Kivinjari chako kimezuia arifa za simu |
| Call notifications enabled | Arifa za simu zimewashwa |
| Call resumed | Simu imeendelea |
| Caller asked for a person | Mpigaji aliomba kuongea na mtu |
| Caller on hold | Mpigaji anasubiri |
| Calls from your assigned queues will appear here. | Simu kutoka foleni zako zitaonekana hapa. |
| Calls handled | Simu zilizohudumiwa |
| Calls paused until your phone reconnects | Simu zimesimamishwa hadi simu yako iunganishwe tena |
| Cancel | Ghairi |
| Cannot reach the server. Check your connection and try again. | Seva haipatikani. Angalia mtandao wako, kisha jaribu tena. |
| Change code | Badilisha msimbo |
| Change it to | Ubadilishe kuwa |
| Choose a queue | Chagua foleni |
| Choose a reason | Chagua sababu |
| Choose a reason before going Not Ready. | Chagua sababu kabla ya kuwa Sipo tayari. |
| Choose a transfer target. | Chagua unayemhamishia simu. |
| Choose a wrap-up code | Chagua msimbo wa kumalizia |
| Choose a wrap-up code to finish this call. | Chagua msimbo wa kumalizia ili umalize simu hii. |
| Complete | Kamilisha |
| Conference | Mkutano wa simu |
| Conference: you, the caller and the other party | Mkutano wa simu: wewe, mpigaji na mtu mwingine |
| Connected | Imeunganishwa |
| Connecting your phone… | Inaunganisha simu yako… |
| Connection history | Historia ya muunganisho |
| Consult first | Shauriana kwanza |
| Consult transfer | Hamisha baada ya kushauriana |
| Consulting {name}; caller on hold | Unashauriana na {name}; mpigaji anasubiri |
| Context | Muktadha |
| Copy these notes before closing this tab. | Nakili maelezo haya kabla ya kufunga ukurasa huu. |
| Could not send digits. Try again. | Imeshindwa kutuma tarakimu. Jaribu tena. |
| Decline | Kataa |
| Digits | Tarakimu |
| Digits sent | Tarakimu zimetumwa |
| Discard draft | Futa rasimu |
| Draft; saved with wrap-up | Rasimu; itahifadhiwa ukimaliza simu |
| Drop the other party | Mtoe mtu mwingine |
| Email | Barua pepe |
| Enable call notifications | Washa arifa za simu |
| English | Kiingereza |
| Everything you need for the next call | Kila unachohitaji kwa simu inayofuata |
| Finish and go Ready | Maliza na uwe Tayari |
| Finish and stay Not Ready | Maliza na ubaki Sipo tayari |
| Finish this call | Maliza simu hii |
| Finish your call and wrap-up before signing out. | Maliza simu na kumalizia kabla ya kutoka. |
| Go Not Ready | Kuwa Sipo tayari |
| Go Not Ready because of: | Kuwa Sipo tayari kwa sababu ya: |
| Go Ready | Kuwa Tayari |
| Go Ready when you can take calls | Kuwa Tayari ukiweza kupokea simu |
| Go Ready when you can take calls. | Kuwa Tayari ukiweza kupokea simu. |
| Handed over by the AI | Imekabidhiwa na AI |
| Hang up | Kata simu |
| Hang up before reconnecting your phone. | Kata simu kabla ya kuunganisha simu yako tena. |
| Hold | Sitisha |
| Incoming call | Simu inaingia |
| Incoming call offer | Simu inayoingia |
| Keep notes factual and relevant. | Andika maelezo ya kweli na yanayohusika. |
| Keypad | Vitufe |
| Language | Lugha |
| Language unknown | Lugha haijulikani |
| Last call's wrap-up code: | Msimbo wa kumalizia wa simu iliyopita: |
| Leave | Ondoka |
| Live: on | Papo hapo: imeunganishwa |
| Live: reconnecting | Papo hapo: inaunganisha tena |
| Logged out | Umetoka |
| Longest | Ndefu zaidi |
| Longest wait {time} | Kusubiri kwa muda mrefu zaidi {time} |
| Mute | Zima sauti |
| My day | Siku yangu |
| My queues | Foleni zangu |
| No AI summary for this call. | Hakuna muhtasari wa AI kwa simu hii. |
| No call | Hakuna simu |
| No code required | Msimbo hauhitajiki |
| Not Ready | Sipo tayari |
| Not ready | Sipo tayari |
| Not Ready after this call | Sipo tayari baada ya simu hii |
| Not Ready reason | Sababu ya kutokuwa tayari |
| Not ready since {time} | Sipo tayari tangu {time} |
| Not ready: Not responding | Sipo tayari: sikujibu simu |
| Not ready: {reason} | Sipo tayari: {reason} |
| Not responding | Sikujibu simu |
| Not signed in. | Hujaingia. |
| Notes | Maelezo |
| Number | Namba |
| On a call | Kwenye simu |
| On hold | Imesitishwa |
| Password | Nenosiri |
| Phone connected | Simu imeunganishwa |
| Phone connecting | Simu inaunganishwa |
| Phone disconnected | Simu imekatika kwenye mtandao |
| Phone not connected | Simu haijaunganishwa |
| Phone: {id} | Simu: {id} |
| Press Answer again to enable call audio. | Bonyeza Pokea tena ili kuwasha sauti ya simu. |
| Queue | Foleni |
| Queue unknown | Foleni haijulikani |
| Ready | Tayari |
| Ready after this call | Tayari baada ya simu hii |
| Reconnect | Unganisha tena |
| Reconnect your phone | Unganisha simu yako tena |
| Reconnect your phone first | Unganisha simu yako kwanza |
| Request failed. Try again. | Ombi limeshindwa. Jaribu tena. |
| Resume | Endelea |
| Return to AlgoDial to answer. | Rudi kwenye AlgoDial ili upokee. |
| Review the caller’s summary above, then answer. | Soma muhtasari wa mpigaji hapo juu, kisha pokea. |
| Ringing {name}; caller on hold | Inampigia {name}; mpigaji anasubiri |
| Search wrap-up codes | Tafuta msimbo wa kumalizia |
| Send digits | Tuma tarakimu |
| Sensitive topic | Suala nyeti |
| Session ended. | Kipindi kimeisha. |
| Shift plus E | Shift pamoja na E |
| Sign in | Ingia |
| Sign in to your agent workspace | Ingia kwenye eneo lako la kazi |
| Sign out | Toka |
| Sign-in failed. | Kuingia kumeshindikana. |
| Signed out. | Umetoka. |
| Summary wrong | Muhtasari si sahihi |
| Swahili | Kiswahili |
| Swahili labels await Tostao’s review | Maneno ya Kiswahili ni rasimu: yanasubiri mapitio ya Tostao |
| Switch colour theme | Badilisha rangi ya ukurasa |
| Thanks. Noted for whoever improves the AI. | Asante. Imehifadhiwa kwa wanaoboresha AI. |
| The AI did not understand | AI haikuelewa |
| The call | Simu |
| The caller’s language, topic and AI summary will appear here when a call arrives. | Lugha ya mpigaji, mada na muhtasari wa AI vitaonekana hapa simu ikiingia. |
| the other party | mtu mwingine |
| Time in wrap-up {time} | Muda wa kumalizia {time} |
| Time remaining to answer | Muda uliobaki kupokea |
| Topic | Mada |
| Transfer | Hamisha |
| Transfer failed. The caller is still with you. | Kuhamisha kumeshindikana. Mpigaji bado yuko nawe. |
| Transfer kind | Aina ya kuhamisha |
| Transfer to | Hamishia kwa |
| Transferred call | Simu iliyohamishwa |
| Two-factor code | Msimbo wa uthibitisho |
| Unknown caller | Mpigaji asiyejulikana |
| Unmute | Washa sauti |
| Unsaved notes from last call | Maelezo ya simu iliyopita ambayo hayajahifadhiwa |
| Use digits 0 to 9, star or hash. | Tumia tarakimu 0 hadi 9, nyota au alama ya #. |
| Wait unknown | Muda wa kusubiri haujulikani |
| Waited {time} | Alisubiri {time} |
| Waiting | Wanaosubiri |
| Waiting for confirmation… | Inasubiri uthibitisho… |
| Waiting for the next call | Inasubiri simu inayofuata |
| Why they called (AI) | Kwa nini wamepiga (AI) |
| Wrap-up | Kumalizia |
| Wrap-up code | Msimbo wa kumalizia |
| Wrap-up code changed | Msimbo wa kumalizia umebadilishwa |
| Wrap-up ends in {time} | Kumalizia kunaisha baada ya {time} |
| Wrap-up time ran out. Open Unsaved notes from last call to copy your draft. | Muda wa kumalizia umeisha. Fungua Maelezo ya simu iliyopita ili unakili rasimu yako. |
| Wrong queue | Foleni si sahihi |
| You can change it for {time} | Unaweza kuubadilisha kwa {time} |
| You missed a call | Hukupokea simu |
| You missed a call, so you were set to Not responding. Press Go Ready when you are back. | Hukupokea simu, kwa hiyo umewekwa Sikujibu simu. Bonyeza Kuwa Tayari ukirudi. |
| Your browser is blocking the microphone. Allow it in the address bar, then press Reconnect. | Kivinjari chako kimezuia maikrofoni. Iruhusu kwenye upau wa anwani, kisha bonyeza Unganisha tena. |
| Your microphone is unavailable. Connect it, allow microphone access, then press Reconnect. | Maikrofoni yako haipatikani. Iunganishe, ruhusu matumizi ya maikrofoni, kisha bonyeza Unganisha tena. |
| Your notes stay alongside the call. | Maelezo yako yanabaki pamoja na simu. |
| Your phone is not connected to the call server. Press Reconnect. If it fails twice, tell your supervisor. | Simu yako haijaunganishwa na seva ya simu. Bonyeza Unganisha tena. Ikishindikana mara mbili, mjulishe msimamizi wako. |
| Your session ended. Please sign in again. | Kipindi chako kimeisha. Tafadhali ingia tena. |
| {count} waiting in your queues | {count} wanasubiri kwenye foleni zako |
| {seconds} seconds left to answer | Sekunde {seconds} zimebaki kupokea |

## Supervisor page (84 labels)

| English | Swahili draft |
| --- | --- |
| a call | simu |
| Actions | Vitendo |
| Agent | Mhudumu |
| All my teams | Timu zangu zote |
| Apply change | Tekeleza mabadiliko |
| Barge | Ingilia |
| Caller | Mpigaji |
| Cancel | Ghairi |
| Change state | Badilisha hali |
| Change {name}’s state | Badilisha hali ya {name} |
| Choose a reason | Chagua sababu |
| Choose a reason before going Not Ready. | Chagua sababu kabla ya kumweka Hayupo tayari. |
| Close | Funga |
| Could not connect. Please try again. | Imeshindwa kuunganisha. Tafadhali jaribu tena. |
| Current call | Simu ya sasa |
| Current supervision | Usimamizi wa sasa |
| Direct call | Simu ya moja kwa moja |
| Duration  | Muda  |
| Email | Barua pepe |
| Incoming call | Simu inaingia |
| Language | Lugha |
| Language unknown | Lugha haijulikani |
| Live calls | Simu zinazoendelea |
| Live: off | Papo hapo: haijaunganishwa |
| Live: on | Papo hapo: imeunganishwa |
| Logged out | Ametoka |
| Longest  | Ndefu zaidi  |
| Longest wait | Kusubiri kwa muda mrefu zaidi |
| Management | Usimamizi |
| Missed a call | Hakupokea simu |
| Monitor | Sikiliza |
| Monitoring {name} | Unamsikiliza {name} |
| Needs attention first | Wanaohitaji kuangaliwa kwanza |
| No agents in this team. | Hakuna wahudumu kwenye timu hii. |
| No recorded events yet. | Bado hakuna matukio yaliyorekodiwa. |
| Not Ready | Hayupo tayari |
| Not ready | Hayupo tayari |
| Not Ready reason | Sababu ya kutokuwa tayari |
| Not responding | Hajibu simu |
| On a call | Kwenye simu |
| On hold | Imesitishwa |
| Over target / answer within {seconds} s | Imezidi lengo / jibu ndani ya sekunde {seconds} |
| Over {minutes}-minute call threshold | Simu imezidi dakika {minutes} |
| Over {minutes}-minute limit | Imezidi kikomo cha dakika {minutes} |
| Password | Nenosiri |
| Phone connected | Simu imeunganishwa |
| Phone connecting | Simu inaunganishwa |
| Phone not connected | Simu haijaunganishwa |
| Queue | Foleni |
| Queue snapshot | Hali ya foleni |
| Ready | Tayari |
| Ready {ready} / Ringing {ringing} / Talking {talking} / Not ready {notReady} / Wrap-up {wrapup} | Tayari {ready} / Inaita {ringing} / Kwenye simu {talking} / Hayupo tayari {notReady} / Kumalizia {wrapup} |
| Reconnect | Unganisha tena |
| Reconnect your phone and live updates before supervising a call. | Unganisha tena simu yako na taarifa za papo hapo kabla ya kusimamia simu. |
| Sign in | Ingia |
| Sign in to your supervisor workspace | Ingia kwenye eneo lako la usimamizi |
| Sign out | Toka |
| Sign out {name} | Mtoe {name} |
| State | Hali |
| State and reason | Hali na sababu |
| Stop | Acha |
| Supervising a call | Unasimamia simu |
| Supervisor workspace | Eneo la kazi la msimamizi |
| Switch colour theme | Badilisha rangi ya ukurasa |
| Switch to barge | Badili uingilie |
| Switch to monitor | Badili usikilize |
| Switch to whisper | Badili unong'one |
| Target: {percent}% answered within {seconds} s | Lengo: asilimia {percent} zijibiwe ndani ya sekunde {seconds} |
| Team | Timu |
| Time | Muda |
| Time in state | Muda katika hali hii |
| Today’s service level | Kiwango cha huduma leo |
| Two-factor code | Msimbo wa uthibitisho |
| View history | Angalia historia |
| Waiting | Wanaosubiri |
| Waiting for confirmation… | Inasubiri uthibitisho… |
| Whisper | Nong'ona |
| Wrap-up | Kumalizia |
| You are in the call: everyone hears everyone. | Uko ndani ya simu: kila mtu anamsikia kila mtu. |
| You hear both; only the agent hears you. The customer does not. | Unawasikia wote wawili; ni mhudumu peke yake anayekusikia. Mteja hakusikii. |
| You hear the customer and the agent. Nobody hears you. | Unamsikia mteja na mhudumu. Hakuna anayekusikia. |
| {count} agents / sorted by attention needed | Wahudumu {count} / wamepangwa kwa uhitaji wa kuangaliwa |
| {count} waiting | {count} wanasubiri |
| {name}’s history | Historia ya {name} |

Management pages and reports stay in English for now: admins use them a few times a week, and Django
ships its own (partial) Swahili for the standard admin words.
