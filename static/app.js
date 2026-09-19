const form = document.getElementById("chat-form");
const messageInput = document.querySelector("textarea");
const chat = document.getElementById("chat");
const chatArea = document.querySelector(".chat-area");
const statusElement = document.getElementById("status");
const submitButton = form.querySelector('button[type="submit"]');

let conversationId = null;

function renderMarkdown(text, container) {
    const lines = text.split("\n");
    let currentList = null;
    function splitTableRow(line) {
        let row = line.trim();
        if (row.startsWith("|")) row = row.slice(1);
        if (row.endsWith("|")) row = row.slice(0, -1);
        return row.split("|").map(cell => cell.trim());
    }

    function addFormattedText(element, value) {
        const parts = value.split(/(\*\*.*?\*\*|\*[^*]+?\*)/g);

        for (const part of parts) {
            if (part.startsWith("**") && part.endsWith("**")) {
                const strong = document.createElement("strong");
                strong.textContent = part.slice(2, -2);
                element.appendChild(strong);
            } else if (part.startsWith("*") && part.endsWith("*")) {
                const emphasis = document.createElement("em");
                emphasis.textContent = part.slice(1, -1);
                element.appendChild(emphasis);
            } else {
                element.appendChild(document.createTextNode(part));
            }
        }
    }

    for (let i = 0; i < lines.length; i++) {
        const trimmedLine = lines[i].trim();

        if (!trimmedLine) {
            currentList = null;
            continue;
        }

        const nextLine = (lines[i + 1] || "").trim();

        if (trimmedLine.includes("|") && nextLine) {
            const headers = splitTableRow(trimmedLine);
            const separators = splitTableRow(nextLine);

            const isTable = (
                headers.length === separators.length &&
                separators.every(cell => /^:?-{3,}:?$/.test(cell))
            );

            if (isTable) {
                currentList = null;

                const wrapper = document.createElement("div");
                wrapper.className = "markdown-table-scroll";

                const table = document.createElement("table");
                const thead = document.createElement("thead");
                const tbody = document.createElement("tbody");
                const headerRow = document.createElement("tr");

                const alignments = separators.map(cell => {
                    if (cell.startsWith(":") && cell.endsWith(":")) return "center";
                    if (cell.endsWith(":")) return "right";
                    return "left";
                });

                headers.forEach((value, column) => {
                    const th = document.createElement("th");
                    th.scope = "col";
                    th.style.textAlign = alignments[column];
                    addFormattedText(th, value);
                    headerRow.appendChild(th);
                });

                thead.appendChild(headerRow);
                table.appendChild(thead);
                table.appendChild(tbody);

                i += 1;

                while (i + 1 < lines.length) {
                    const rowLine = lines[i + 1].trim();

                    if (!rowLine || !rowLine.includes("|")) break;

                    const cells = splitTableRow(rowLine);

                    if (cells.length !== headers.length) break;

                    const tr = document.createElement("tr");

                    cells.forEach((value, column) => {
                        const td = document.createElement("td");
                        td.style.textAlign = alignments[column];
                        addFormattedText(td, value);
                        tr.appendChild(td);
                    });

                    tbody.appendChild(tr);
                    i += 1;
                }

                wrapper.appendChild(table);
                container.appendChild(wrapper);
                continue;
            }
        }

        if (trimmedLine.startsWith("## ")) {
            currentList = null;

            const heading = document.createElement("h2");
            addFormattedText(heading, trimmedLine.slice(3));
            container.appendChild(heading);

            continue;
        }

        if (trimmedLine.startsWith("### ")) {
            currentList = null;

            const heading = document.createElement("h3");
            addFormattedText(heading, trimmedLine.slice(4));
            container.appendChild(heading);

            continue;
        }

        const orderedMatch = trimmedLine.match(/^\d+\.\s+(.*)$/);
        const unorderedMatch = trimmedLine.match(/^[-*]\s+(.*)$/);

        if (orderedMatch) {
            if (!currentList || currentList.tagName !== "OL") {
                currentList = document.createElement("ol");
                container.appendChild(currentList);
            }

            const item = document.createElement("li");
            addFormattedText(item, orderedMatch[1]);
            currentList.appendChild(item);
            continue;
        }

        if (unorderedMatch) {
            if (!currentList || currentList.tagName !== "UL") {
                currentList = document.createElement("ul");
                container.appendChild(currentList);
            }

            const item = document.createElement("li");
            addFormattedText(item, unorderedMatch[1]);
            currentList.appendChild(item);
            continue;
        }

        currentList = null;

        const paragraph = document.createElement("p");
        addFormattedText(paragraph, trimmedLine);
        container.appendChild(paragraph);
    }
}

function scrollChatToBottom() {
    chatArea.scrollTo({
        top: chatArea.scrollHeight,
        behavior: "smooth"
    });
}

form.addEventListener("submit", async function (event) {
    event.preventDefault();

    const message = messageInput.value.trim();

    if (!message) {
        return;
    }

    const messageElement = document.createElement("div");

    messageElement.className = "message user-message";

    messageElement.innerHTML = `
        <div class="message-label">You</div>
        <div class="message-content"></div>
        <div class="message-end"></div>
    `;

    messageElement.querySelector(".message-content").textContent = message;

    chat.appendChild(messageElement);

    messageInput.value = "";

    scrollChatToBottom();

    statusElement.innerHTML = `
        <div class="typing-label">Patent Agent</div>
        <div class="typing-indicator" aria-hidden="true">
            <span></span>
            <span></span>
            <span></span>
        </div>
    `;

    scrollChatToBottom();

    submitButton.disabled = true;
    messageInput.disabled = true;

    try {
        const response = await fetch("/chat", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                message: message,
                conversation_id: conversationId
            })
        });

        const data = await response.json();

        if (!response.ok) {
            statusElement.textContent = data.detail || "Something went wrong.";
            return;
        }

        conversationId = data.conversation_id;

        const agentMessageElement = document.createElement("div");

        agentMessageElement.className = "message agent-message";

        agentMessageElement.innerHTML = `
            <div class="message-label">Patent Agent</div>
            <div class="message-content"></div>
            <div class="message-end"></div>
        `;

        const agentContent = agentMessageElement.querySelector(".message-content");

        renderMarkdown(data.answer, agentContent);

        chat.appendChild(agentMessageElement);

        statusElement.textContent = "";

        scrollChatToBottom();

    } catch (error) {
        statusElement.textContent = "Connection error. Please try again.";
    } finally {
        submitButton.disabled = false;
        messageInput.disabled = false;
        messageInput.focus();
    }
});

messageInput.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
    }
});