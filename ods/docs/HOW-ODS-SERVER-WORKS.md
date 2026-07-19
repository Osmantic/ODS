# ODS — A Friendly Guide

---

Let me tell you about a problem most people don't know they have.

Every time you open up one of those AI chat windows — the ones everybody's talking about right now — and you type a question, something happens that you probably haven't thought much about. Your words leave your computer. They travel across the internet to a server somewhere — could be in Virginia, could be in Oregon, could be overseas — and a company you didn't hire, and didn't really choose, reads what you wrote, thinks about it, and sends something back.

Now most of the time, that's fine. You're asking about the weather, or how to spell a word, or what to make for dinner. Nothing sensitive. No harm done.

But a lot of people — smart people, careful people, people running businesses and nonprofits and medical practices and law firms — start thinking about that arrangement a little more carefully, and they get uncomfortable. Because they're not just asking about dinner recipes. They're talking about clients. About patients. About strategies and finances and private conversations that were never meant to leave the room.

And here's the other thing. Even if you don't care about privacy — even if you're fine with all of it — you're still renting. Every month. Every message. And those bills have a way of growing.

ODS is the answer to both of those problems. It's a complete artificial intelligence system — everything you get from the fancy cloud tools, the chat, the voice, the image generation, the research assistant, the workflow automation — all of it running on a computer that you own, in a location you control, on a network that goes nowhere unless you tell it to.

And here's what makes it remarkable. You don't build it piece by piece. You don't spend a weekend reading documentation and debugging why two programs can't find each other. You run one command. You wait fifteen or twenty minutes. And then you open a web browser and you have a complete, working AI system.

Let me tell you how it all fits together.

---

## What We're Actually Talking About

When people say "AI" these days, they usually mean one specific thing: a language model. That's the technology that reads your question and writes a response. It's been trained on enormous amounts of text — books, articles, websites, conversations — and through that training it's developed something that looks and feels remarkably like understanding.

Now, the version of this that most people use lives on someone else's computer. You rent access to it. The version that ODS installs lives on your computer. Same basic idea, different address.

You gotta realize — the language model by itself isn't enough. It's like having a brilliant mind with no way to hear you, no way to speak, no way to look things up, no way to remember what it learned yesterday. Useful in theory. Limited in practice.

So ODS bundles everything together. The language model, yes. But also the chat interface you talk to it through. The voice system so you can speak to it and hear it respond. The search engine so it can look up current information without sending your questions to Google. The document system so it can read your files and actually learn from them. The workflow engine so it can take action and automate things on your behalf. The image generator. The agent that can plan multi-step tasks and execute them on its own.

All of it. Pre-wired. Pre-configured. Ready to go.

That's ODS.

---

## The Team in the Building

I find it helps to think of ODS as an office building. And inside that building, there's a team. Each person on the team has a specific job. They don't step on each other's work. They pass things back and forth through a shared hallway. And when you walk in the front door, you don't need to know how the whole place is organized. You just talk to whoever you need.

Let me introduce you to the team.

The first person you meet is **Open WebUI**. That's the front desk. It's what you see when you open your browser and navigate to the address ODS gives you. It looks, honestly, like ChatGPT. Same basic layout. You type a message, you get a response. Or you speak a message and hear a response, if you've got voice turned on. Open WebUI is handling your conversation history, letting you upload files, connecting you to web search when you need current information. It's your interface to everything else.

But Open WebUI isn't doing the thinking. When you type a question, it passes that question down the hall to someone else.

That someone else is called **llama-server**, and it's the brain. This is where the AI model lives — loaded into the memory of your graphics card, ready to read what you wrote and generate a response. It works word by word, streaming its answer back in real time, which is why you see the text appearing gradually instead of all at once. It's fast. On good hardware, it's producing a hundred words a second or more.

Now, llama-server speaks a language that a lot of other tools already understand — it's the same format that OpenAI uses for their API. Which means any application built to work with ChatGPT can be pointed at your local ODS instead, and it'll work. That's a bigger deal than it sounds. We'll come back to it.

Sitting between your chat interface and the brain is a service called **LiteLLM**. Think of LiteLLM as a very smart switchboard operator. When your question comes in, LiteLLM decides where to route it. Most of the time, it goes straight to your local AI. But if you want, you can configure it so that certain kinds of questions go to a cloud model instead — Claude, or GPT-4 — while everything else stays local. Or you can tell it: local is always first, but if local is busy or struggling, fall back to the cloud automatically. This hybrid approach gives you flexibility without forcing you to choose all-or-nothing.

Down another hall, there's **SearXNG**. This is your personal search engine. When you ask your AI something that requires current information — today's news, recent research, anything that happened after the AI's training data was collected — it sends a search query to SearXNG, which goes out and searches Google, DuckDuckGo, Wikipedia, GitHub, and more, then brings the results back. All without logging your searches. All without building an advertising profile around what you're curious about. The AI reads those results, synthesizes them, and gives you an informed answer. That's the search loop.

Now we get to the services you turn on depending on what you need.

If you want to talk instead of type, you need two people on the team. The first is **Whisper** — named for the way it quietly converts your spoken words into text. You say something, Whisper transcribes it precisely, and passes those words to the chat interface as if you'd typed them. It's accurate in a way that'll surprise you. It handles accents. It handles people who talk fast. It handles ambient noise reasonably well. And it runs entirely on your hardware — the audio of your voice never leaves your machine.

The second is **Kokoro**. Kokoro does the reverse. Once the AI has composed its response, Kokoro speaks it aloud in a voice that sounds warm and natural. Nothing like the robotic voices of old automated phone systems. When Whisper and Kokoro are both running, you have a full voice conversation loop. You talk. It listens. It thinks. It talks back. You don't have to touch a keyboard at all.

Then there's **n8n**. And n8n is remarkable. It's a workflow automation platform with connections to over four hundred different services and applications. The way to understand n8n is to think about all the things that happen after a conversation ends. Maybe you want a summary saved to a Google Doc. Maybe you want an email sent. Maybe you want a task created in your project management system. Maybe you want information from a conversation automatically stored in a database. n8n is how all of that happens. It has a visual editor — you drag and drop pieces together like a flowchart — and you can build automated pipelines without writing any code. On your own machine. Using your local AI as the brain.

**Qdrant** is the team's librarian. If you've ever wanted to give an AI access to your actual documents — your PDFs, your reports, your notes, your research — Qdrant is what makes that possible. You feed it documents, and it stores them in a way that lets the AI search through them by meaning rather than just by keywords. You ask, "what did we decide about the vendor contract in March?" and the AI doesn't search for the words "vendor contract March." It searches for the meaning of that question across everything you've given it, and it surfaces the relevant passages. This is called retrieval-augmented generation, and it transforms your local AI from a general-purpose assistant into an expert on your specific content.

Working alongside Qdrant is the **Embeddings** service. This is the translator that converts your text documents into a form that Qdrant can search. You don't interact with it directly. It just does its work quietly in the background.

For anyone who wants to generate images from text descriptions, there's **ComfyUI**. It's a sophisticated visual interface for image generation using SDXL Lightning — and once it's set up, your chat interface can send image generation requests to it automatically. You type "generate an image of a sun setting over a mountain lake," and a few seconds later you have one. Entirely local. No subscription to Midjourney or DALL-E required.

**Hermes Agent** is the default autonomous agent. Here's how to think about the difference between the chat interface and an autonomous agent. In the chat, you ask one question at a time and the AI responds. In Hermes, you give the AI a goal — "research this topic, find the three best sources, write me a summary, and save it to a file" — and it figures out the steps on its own, uses tools, browses the web, takes actions, and comes back to you when the task is done. It is reached through the Hermes auth proxy rather than a direct public port. OpenClaw is still available for older workflows, but it is deprecated and no longer the default path.

And finally there's the **Dashboard**. This is the control room. You open it in your browser and you can see at a glance which services are running, which ones have issues, how hard the GPU is working, how much memory is in use, and the health of the whole system. It's calm and clear and makes the whole operation feel manageable even if you're not technical.

That's the team. The exact roster depends on which optional services you've turned on; the current product tree has two dozen bundled service manifests, plus a few host helpers for native inference and developer tools. They all work together, all day, without you having to think about any of it.

---

## How the Installer Works

Now I want to tell you about the installation, because this is where ODS earns its name.

Setting up a system like this from scratch used to mean installing each piece separately. Then configuring them to find each other. Then debugging why they couldn't. Then starting over when something broke. That's a weekend. Maybe two weekends. It's the kind of thing that makes technically capable people give up and go back to paying a monthly subscription. Not because they can't figure it out, but because life is short and there are better ways to spend a Saturday.

The ODS installer compresses all of that into fifteen to thirty minutes.

Here's what it does. You run one command, and the installer starts working through a sequence of steps. First, it checks your system — makes sure you have the right software, the right amount of disk space, the right kind of network setup. Then it looks at your hardware. Specifically, it looks at your graphics card and asks two questions: what kind is it, and how much memory does it have?

The answer to those questions determines which AI model you get. And here's the thing — it doesn't install the same model on every machine. A graphics card with eight gigabytes of memory gets a seven-billion-parameter model — fast, capable, excellent for everyday use. A card with twenty-four gigabytes gets a larger, more capable model. A card with forty-eight or ninety-six gigabytes gets the most powerful models available. The system matches the model to the hardware automatically. You never have to understand what a "parameter" is or make that decision yourself.

Then it asks you which optional features you want. Voice? Workflow automation? Document search? Image generation? Autonomous agents? You answer a few questions, and the installer knows exactly what to set up.

It generates secure passwords for all the services that need them. It pulls down the Docker containers — these are self-contained packages, like pre-assembled rooms, one for each service. It starts everything up, wires the services together, and runs a health check on each one to confirm it's working properly.

And then, if you're on a slower internet connection or just want to get started right away, it does something clever. It first downloads a small, lightweight model — small enough to be running within two minutes — so you can start chatting while the full model continues downloading in the background. When the full model finishes, it swaps it in automatically. You don't have to do anything.

At the end, you have a running system. You open your browser, you go to the address the installer gives you, and you type something. And something answers. From a machine sitting in your own building. That first response — I don't care how technical you are — it lands differently than you expect.

---

## Living With It Day to Day

Now, I have to warn you — this next part is going to sound a little funny. Because I'm going to read you some commands. Things you type into a terminal. And reading computer commands out loud is, frankly, a little silly. But stay with me, because what they do is simple, and once you've heard them once you'll remember them.

After that first installation, ODS mostly just runs. You don't maintain it the way you maintain a car. You don't tune it the way you tune an instrument. It's more like a refrigerator — you turn it on, you use it, and most days you don't think about it at all.

When you do need to manage it, you use a single word: **ods**. That's the name of the tool. You open a terminal window — think of it as a text-based way to talk directly to your computer — and you type the word ods, followed by an instruction.

The one you'll use most is **ods status**. It shows you what's running, what's healthy, and how hard the GPU is working. It's your quick health check. If something feels off, that's the first place you look.

**ODS start** and **ods stop** do exactly what they sound like. The whole system, up or down, one word.

**ODS list** shows you every service that's available — which ones are on, which ones are sitting there waiting to be turned on.

And **ods logs** followed by the name of a service — say, ods logs whisper, or ods logs llm — shows you a live window into what that service is doing. It's how you check in on a specific piece of the system if something doesn't seem right.

Now, there are three commands that change how the whole system thinks about where your AI lives. You can tell it **ods mode local** — everything runs on your machine, nothing touches the cloud. That's the default. That's the privacy setting. Or **ods mode cloud**, which routes the language model out to an API like Claude or GPT-4 — useful if you want the full ODS ecosystem but don't have the GPU to run a model locally. Or **ods mode hybrid**, which is local first, cloud as a fallback. The system decides.

And if you ever want to try a different AI model — a bigger one, a faster one, something that just came out — **ods model list** shows you what's available for your hardware, and **ods model swap** followed by the tier name makes the switch.

That's it. That's the whole thing. Most days you just open your browser and chat. The commands are there for when you need them. You don't need to memorize them. They'll come back to you the same way anything comes back to you — when the moment arrives, you'll remember.

---

## The Architecture That Makes It All Work

Now, there's something about how these services actually talk to each other that I think is worth understanding — because it explains why the system is so reliable, and why it's so easy to make it your own.

Every service in ODS runs in something called a Docker container. Here's how to understand that. Imagine each service is a living space — an apartment. Each apartment has everything it needs to function: its own plumbing, its own electricity, its own walls. They don't share any of that infrastructure. If there's a water problem in apartment three, apartment seven doesn't flood.

Docker containers work the same way. Each service has its own isolated environment. If one service crashes or needs to be restarted, the others keep running. If you want to update one service, you update just that container without touching anything else. If you want to remove a service entirely, you remove the container and there's nothing left behind — no leftover files scattered around your system, no registry entries, no residue.

They communicate with each other through a shared network — but only with each other. From the outside, from the internet, most services aren't visible at all. Only the ones that need to be — your chat interface, your dashboard — are accessible through your browser.

When the services find each other, they use names rather than addresses. Open WebUI reaches the language model at the address "llama-server." It reaches the search engine at "searxng." These names always resolve correctly because they're all on the same internal network. You never have to configure IP addresses or port numbers for the services to find each other. That's all handled automatically.

Now here's what I want you to understand about the extension system, because this is where ODS becomes something genuinely different from a fixed piece of software.

Every service in the system — not just the optional ones, but all of them, including the core services — follows the same pattern. Each service lives in its own folder. Inside that folder are two files. One file describes the service: its name, what port it uses, how to check if it's healthy, what other services it depends on. The other file tells Docker how to run it.

That's all it takes for ODS to know about a service. It reads those two files and automatically adds the service to the command-line tool, to the dashboard, to the health check system, to the installer. There's no central list of approved services that has to be updated. There's no special registration process. You put a folder in the right place with those two files, and the system discovers it on its own.

What this means in practice is that ODS is genuinely moddable in a way most software isn't. If you find a tool you like — some piece of software that does something useful — and it's available as a Docker container, you can add it to ODS in about fifteen minutes. It'll show up in your service list. It'll show up in your dashboard. It'll be managed by the same command-line tool as everything else. Your custom service and the built-in services are equals.

And disabling a service is even simpler than that. The system checks whether a certain file is present or not. Present means enabled. Not present means disabled. When you run ods disable followed by a service name, the system renames that file. When you run ods enable, it renames it back. That renaming is literally all that happens. Which means it's reversible, instant, and impossible to get wrong.

---

## What Happens When You Ask a Question

Let me walk you through what actually happens, technically, when you type a message into your ODS chat interface. Not because you need to know this to use it, but because understanding it makes the whole system feel less like magic and more like craft.

You type: "What's happening with interest rates right now?"

Open WebUI receives that message. It recognizes that this is a question about current events — something that happened after the AI's training data was collected. So it doesn't send your question straight to the language model. First, it sends a search query to SearXNG.

SearXNG takes that query, goes out to multiple search engines simultaneously — Google, DuckDuckGo, Brave, others — collects the most relevant results, and brings them back. This takes a second or two.

Now Open WebUI has your question and a set of current search results. It bundles them together into a single, larger message that essentially says: "Here's some current information about this topic. Given that information, please answer this question thoughtfully." That combined message goes to the language model.

The language model reads all of it — your question, the search results, the full context of your previous conversation — and begins generating a response. Word by word, streaming it back to Open WebUI, which displays it in your browser in real time.

The whole thing, from the moment you hit enter to the moment the response begins appearing, is typically a second or two. For a five-sentence response, the full text might take another three to five seconds to complete.

If you're using voice, Whisper stepped in at the beginning to convert your spoken words into the text that started this whole chain. And Kokoro steps in at the end, taking the completed text response and reading it aloud to you while it's still being generated.

And when the conversation is over, if you have n8n configured to capture summaries or extract information, it receives a webhook — a notification — from the agent framework, picks up the conversation data, and routes it wherever you've told it to go. Into a database. Into a document. Into an email. Into your calendar. Whatever you've automated.

That's one message. From your mouth to your ears, or your fingers to your eyes, with six or seven services working together invisibly to make it feel like talking to one very capable mind.

---

## Why This Matters Beyond the Technology

I've been talking about services and containers and models and ports. Let me step back for a moment and talk about what's actually at stake.

There's a shift happening right now in how artificial intelligence is distributed. For the past several years, the assumption has been that AI is something you access. Like electricity from a utility — you plug in, you use it, you pay the bill. Most people accepted that without questioning it. I did too, for a while. It took building something like this to understand that the arrangement was never inevitable. It was just the first way somebody figured out how to sell it.

ODS is part of a different story. The story that says AI capability can be owned. That the tools, the models, the inference engines, the workflow systems — all of this can run on hardware in your possession, under your policies, without a monthly invoice arriving from somewhere else.

For a nonprofit, that means donor conversations and client case notes and strategic planning discussions never leave your network. For a medical practice, that means patient information stays where it belongs. For a law firm, that means privileged communications remain privileged. For a small business, that means the competitive intelligence you're building through AI assistance isn't being processed by the same company that processes your competitor's intelligence.

And for anyone who's been burned by a software subscription that doubled in price, or a service that changed its terms, or a platform that got acquired and changed everything — for anyone who's made the mistake of building something important on someone else's foundation — ODS is the alternative. Your foundation. Your hardware. Your data. Your control.

The models themselves are free. Open source, trained by research institutions and companies who've released them for anyone to use. You download them once. They run forever. Your per-conversation cost, after the initial hardware investment, is essentially the electricity to run a computer that you probably already own.

There's a word that gets used in discussions about this kind of infrastructure: sovereignty. Data sovereignty. Digital sovereignty. It's a strong word, and it's the right one. Because what ODS gives you, at its core, is the ability to make decisions about your own information without asking permission from anyone.

---

## Making It Your Own

Now here's something I find genuinely exciting about this system, and I want to make sure you understand it before we move on.

ODS isn't one of those things where someone hands you a finished product and says use it this way. It's built to be taken apart and put back together differently. Every piece of it is adjustable. And you don't need to be a programmer to do most of it.

The place to start is a single file called the env file. You'll see it written with a dot in front — .env — which just means it's a settings file. Think of it as a dashboard in a room where all the knobs and switches are labeled in plain English. This model or that one. This much memory or that much. Local mode, cloud mode, hybrid mode. Your passwords. Your port numbers. Every setting that controls how ODS behaves lives in that one file, and every setting has a note next to it explaining what it does.

If you only ever touch one thing under the hood, that's the one.

Beyond that, each service has its own adjustments. The search engine lets you decide which sources it pulls from. The AI's personality — how it responds, how formal or casual it sounds, how creative or careful it is — can be shaped through what are called system prompts, which are just instructions you write in plain language and hand to the model.

And then there's n8n. I'd encourage you to spend an afternoon with it when you're ready. You open it in your browser, and it shows you a canvas where you can drag and drop pieces of a workflow together — like building a little assembly line out of blocks. Each block does one thing. Receives an email. Summarizes a document. Saves something to a folder. Sends a notification. And your local AI is available as one of those blocks. Which means you can build processes that think — not just processes that move data around. Once you see what's possible, you start looking at repetitive tasks in your life and asking could that be a workflow? And the answer, more often than not, is yes.

The last thing I'll say about customization is the part that impresses me most. If you find a piece of software somewhere — something that runs in Docker — you can add it to ODS in about fifteen minutes. Two small files in a folder, and the system discovers it on its own. Shows up in your service list. Shows up in your dashboard. Gets managed alongside everything else. Your addition and the built-in services are treated exactly the same. There's no special permission you need to ask for. There's no list of approved tools. You just add it, and it works.

That's a different philosophy than most software you've used. Most software says here's what you get. ODS says here's a starting point.

---

## What You Need to Get Started

Let me be straightforward about the hardware requirements, because this is where people sometimes get tripped up.

ODS runs on a standard computer with a modern graphics card. The graphics card is important — not for displaying graphics in this case, but because it has a special kind of memory and processing architecture that makes running AI models dramatically faster. The difference between a good GPU and no GPU isn't a little bit faster. It's the difference between responses that take two seconds and responses that take two minutes. The GPU matters.

The right GPU for you depends on how you want to use the system. For personal or small team use — one or two people chatting, occasional document analysis — an RTX 5090 or similar card with thirty-two gigabytes of memory runs a capable model and gives you a good experience. For heavier use, or if you want to run more capable models, stepping up to a card or system beyond that opens significantly more options. For professional deployments — teams of people, complex workflows, the most capable open-source models — cards with ninety-six gigabytes run everything ODS offers without constraint.

The rest of the hardware is ordinary. A modern processor. Enough regular memory — thirty-two gigabytes is comfortable. Fast storage, because large model files load faster from a solid-state drive.

And you need to be comfortable enough with a computer to open a terminal window and type a command. That's genuinely the technical bar for installation. After that, everything is a browser interface.

---

## The Bigger Picture

Here's what I want you to walk away with.

ODS isn't a product in the conventional sense. It's not something you buy, and it's not something you subscribe to. It's a system — a carefully designed arrangement of excellent, free, open-source tools, wired together so that you don't have to do the wiring yourself.

The tools themselves — the language model, the voice transcription, the text-to-speech, the search engine, the workflow automation, the vector database, the image generator — all of these exist because communities of researchers and engineers decided to build powerful technology and release it freely. ODS's contribution is integration. Taking those separate pieces and making them one thing that works without friction.

What you get on the other side of that fifteen-minute installation is something genuinely remarkable. A private AI system that you own outright. That improves as you learn to use it. That can be customized for whatever you actually need. That costs nothing to run beyond electricity. That doesn't phone home, doesn't train on your conversations, doesn't raise its prices, doesn't change its terms, doesn't get acquired and pivoted into something unrecognizable.

Your AI. Your data. Your machine. Your rules.

---

*ODS is an open-source project by [Osmantic](https://github.com/Osmantic). For documentation, source code, and community support: [github.com/Osmantic/ODS](https://github.com/Osmantic/ODS)*
