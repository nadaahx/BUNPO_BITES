
const grammarInput = document.getElementById("grammar");
const situationElement = document.getElementById("situation");
const answerInput = document.getElementById("answer");
const feedbackElement = document.getElementById("feedback");
const practice = document.getElementById("practice");

const generateButton = document.getElementById("generate");
const checkButton = document.getElementById("check");
const nextButton = document.getElementById("next");



let currentSituation = "";

async function apiRequest(url, body) {
    const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
    });

    const text = await response.text();
    let data;

    try {
        data = JSON.parse(text);
    } catch {
        throw new Error(`Unexpected server response: ${text.slice(0, 200)}`);
    }

    if (!response.ok) {
        throw new Error(data.detail || "Request failed.");
    }

    return data;
}


/* ======================================
   BUNPO BITES — PAGE NAVIGATION
   Append to the bottom of script.js
   ====================================== */

(() => {
    const pages = [
        {
            id: 1,
            caption: "A warm welcome"
        },
        {
            id: 2,
            caption: "The practice menu"
        },
        {
            id: 3,
            caption: "Your conversation corner"
        }
    ];

    let currentPage = 1;

    const pageElements = pages.map(({ id }) =>
        document.getElementById(`page-${id}`)
    );

    const pageDots = document.querySelectorAll(".page-dot");
    const prevButton = document.getElementById("prev-page");
    const nextButton = document.getElementById("next-page");
    const caption = document.getElementById("nav-caption");

    const practice = document.getElementById("practice");
    const emptyState = document.getElementById("conversation-empty");
    const generateButton = document.getElementById("generate");
    const homeLink = document.getElementById("home-link");

    function showPage(pageNumber) {
        const target = Math.max(1, Math.min(3, pageNumber));

        currentPage = target;

        pageElements.forEach((page, index) => {
            const isActive = index + 1 === target;

            page.classList.toggle("active", isActive);
            page.setAttribute("aria-hidden", String(!isActive));
        });

        pageDots.forEach((dot, index) => {
            const step = index + 1;

            dot.classList.toggle("active", step === target);
            dot.classList.toggle("completed", step < target);

            if (step === target) {
                dot.setAttribute("aria-current", "step");
            } else {
                dot.removeAttribute("aria-current");
            }
        });

        prevButton.disabled = target === 1;
        nextButton.disabled = target === 3;

        caption.textContent = pages[target - 1].caption;

        // Start each page at its top.
        window.scrollTo({
            top: 0,
            behavior: "smooth"
        });
    }

    // Clickable numbered steps.
    pageDots.forEach(dot => {
        dot.addEventListener("click", () => {
            showPage(Number(dot.dataset.go));
        });
    });

    // Buttons and links that use data-go.
    document.querySelectorAll("[data-go]").forEach(button => {
        button.addEventListener("click", () => {
            showPage(Number(button.dataset.go));
        });
    });

    // Previous and next arrows.
    prevButton.addEventListener("click", () => {
        showPage(currentPage - 1);
    });

    nextButton.addEventListener("click", () => {
        showPage(currentPage + 1);
    });

    // Brand logo returns to the welcome page.
    homeLink.addEventListener("click", event => {
        event.preventDefault();
        showPage(1);
    });

    // Generating a situation takes the user to page 3.
    generateButton.addEventListener("click", () => {
        showPage(3);
    });

    // Show the empty state until a conversation is generated.
    function syncConversationState() {
        const hasConversation =
            practice && !practice.classList.contains("hidden");

        emptyState.classList.toggle("hidden", Boolean(hasConversation));
    }

    if (practice && emptyState) {
        const observer = new MutationObserver(syncConversationState);

        observer.observe(practice, {
            attributes: true,
            attributeFilter: ["class"]
        });

        syncConversationState();
    }

    // Allow keyboard navigation between the three pages.
    document.addEventListener("keydown", event => {
        if (
            event.altKey &&
            !event.ctrlKey &&
            !event.metaKey &&
            !event.shiftKey
        ) {
            if (event.key === "ArrowLeft" && currentPage > 1) {
                showPage(currentPage - 1);
            }

            if (event.key === "ArrowRight" && currentPage < 3) {
                showPage(currentPage + 1);
            }
        }
    });

    // Start on the welcome page.
    showPage(1);
})();


async function generateSituation() {
    const grammar = grammarInput.value.trim();

    if (!grammar) {
        alert("Please enter a Japanese grammar rule.");
        return;
    }

    generateButton.disabled = true;
    nextButton.disabled = true;
    generateButton.textContent = "Generating...";
    situationElement.textContent = "Creating your conversation...";
    feedbackElement.classList.add("hidden");
    practice.classList.remove("hidden");
    answerInput.value = "";

    try {
        const data = await apiRequest("/api/generate-situation", { grammar });
        currentSituation = data.situation;
        try {
    await renderFurigana(currentSituation);
} catch (error) {
    // Keep the original conversation available if conversion fails.
    situationElement.textContent = currentSituation;
}
    } catch (error) {
        situationElement.textContent = "Error: " + error.message;
    } finally {
        generateButton.disabled = false;
        nextButton.disabled = false;
        generateButton.textContent = "Generate Situation";
    }
}

async function checkAnswer() {
    const grammar = grammarInput.value.trim();
    const answer = answerInput.value.trim();

    if (!currentSituation) {
        alert("Generate a situation first.");
        return;
    }

    if (!answer) {
        alert("Please write your Japanese reply.");
        return;
    }

    checkButton.disabled = true;
    checkButton.textContent = "Checking...";
    feedbackElement.textContent = "Checking your Japanese...";
    feedbackElement.classList.remove("hidden");

    try {
        const data = await apiRequest("/api/check-answer", {
            grammar,
            situation: currentSituation,
            answer
        });

        feedbackElement.textContent = data.feedback;
    } catch (error) {
        feedbackElement.textContent = "Error: " + error.message;
    } finally {
        checkButton.disabled = false;
        checkButton.textContent = "Check My Japanese";
    }
}


async function renderFurigana(text) {
    const data = await apiRequest("/api/furigana", { text });

    situationElement.replaceChildren();

    const kanjiPattern = /[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]/;

    for (const segment of data.segments) {
        if (!kanjiPattern.test(segment.text)) {
            situationElement.appendChild(
                document.createTextNode(segment.text)
            );
            continue;
        }

        const ruby = document.createElement("ruby");
        ruby.className = "kanji-word";
        ruby.title = "Click to show hiragana";

        ruby.appendChild(document.createTextNode(segment.text));

        const reading = document.createElement("rt");
        reading.textContent = segment.reading;
        ruby.appendChild(reading);

        ruby.addEventListener("click", () => {
            ruby.classList.toggle("show-reading");
        });

        situationElement.appendChild(ruby);
    }
}


generateButton.addEventListener("click", generateSituation);
nextButton.addEventListener("click", generateSituation);
checkButton.addEventListener("click", checkAnswer);
