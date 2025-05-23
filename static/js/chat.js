let isSubmitting = false;
let isCreatingChat = false;

// Add this constant at the top of your file to define the desired order
const METADATA_ORDER = [
    'issue_title',
    'category',
    'incident_number',
    'confidence',
    'time_since_created',
    'detected_language'
];

function showLoading() {
    document.getElementById('loadingIndicator').style.display = 'flex';
}

function hideLoading() {
    document.getElementById('loadingIndicator').style.display = 'none';
}

// Update the formatTime helper function to handle invalid dates
function formatTime(isoString) {
    if (!isoString) return '';
    try {
        return new Date(isoString).toLocaleTimeString([], { 
            hour: '2-digit', 
            minute: '2-digit' 
        });
    } catch (e) {
        console.error('Invalid date format:', isoString);
        return '';
    }
}

async function sendMessage() {
    const userInput = document.getElementById('user-input');
    const chatContainer = document.getElementById('chatContainer');
    const message = userInput.value.trim();
    
    if (!message || isSubmitting) return;

    // Set submission lock
    isSubmitting = true;
    const sendButton = document.getElementById('sendButton');
    sendButton.disabled = true;

    // Add user message to chat
    const userMessageHtml = `<div class="message user-message">${message.trim()}</div>`.trim();
    chatContainer.insertAdjacentHTML('beforeend', userMessageHtml);

    // Clear input field and reset height
    userInput.value = '';
    userInput.style.height = 'auto';

    // Add loading animation
    const loadingHtml = `<div class="message assistant-message"><div class="loading-dots"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div></div>`;
    chatContainer.insertAdjacentHTML('beforeend', loadingHtml);
    chatContainer.scrollTop = chatContainer.scrollHeight;

    try {
        // Create FormData and append message
        const formData = new FormData();
        formData.append('question', message);

        const response = await fetch('/message', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        console.log('Received response data:', data); // Debug log
        
        // Remove loading animation
        const loadingElement = document.querySelector('.loading-dots')?.parentElement;
        if (loadingElement) {
            loadingElement.remove();
        }

        // Only add assistant's response if we have valid data
        if (data && data.response) {
            const responseTime = new Date().toISOString();
            const displayResponseTime = formatTime(responseTime);
            
            // Create citations HTML if there are citations
            let citationsHtml = '';
            if (data.citations && data.citations.length > 0) {
                const uniqueUrls = new Set();
                citationsHtml = `<div class="citations-container"><div class="citations-grid">${data.citations.filter(citation => {
                    if (uniqueUrls.has(citation.url)) return false;
                    uniqueUrls.add(citation.url);
                    return true;
                }).map(citation => `<div class="citation-tile">
                    <div class="citation-preview">${/\.(doc|docx|xls|xlsx|ppt|pptx)$/i.test(citation.url) ? 
                        `<div class="office-doc-preview">
                            <img class="office-doc-icon" src="/static/images/m365.svg" alt="Microsoft Office document icon">
                        </div>` : 
                        `<iframe class="document-preview" src="${citation.url}" title="Preview of ${citation.title || citation.filepath || 'Untitled Source'}"></iframe>`}
                    </div>
                    <div class="citation-details">
                        <div class="citation-title">${citation.title || citation.filepath || 'Untitled Source'}</div>
                        <a href="${citation.url}" class="citation-link" target="_blank">Open Document</a>
                    </div>
                </div>`).join('')}</div></div>`;
            }

            // Update assistant message HTML
            const assistantMessageHtml = `<div class="message assistant-message">${data.response.trim()}${citationsHtml}</div>`.trim();
            chatContainer.insertAdjacentHTML('beforeend', assistantMessageHtml);

            // Update metadata if provided
            if (data.metadata) {
                console.log('Updating metadata with:', data.metadata); // Debug log
                try {
                    updateMetadata(data.metadata);
                } catch (metadataError) {
                    console.error('Error updating metadata:', metadataError);
                    // Don't throw the error to the user, just log it
                }
            }

            // Update conversation title if provided
            if (data.metadata && data.metadata.focus_area) {
                updateConversationTitle(data.metadata.focus_area);
            }
        }

    } catch (error) {
        console.error('Full error details:', error); // Enhanced error logging
        
        // Remove loading animation if it exists
        const loadingElement = document.querySelector('.loading-dots')?.parentElement;
        if (loadingElement) {
            loadingElement.remove();
        }
        
        // Create a more user-friendly error message
        let errorMessage = 'An error occurred while processing your request.';
        if (error.message.includes('Cannot read properties of undefined')) {
            errorMessage = 'There was an issue loading the response data. Please try again.';
        } else if (!navigator.onLine) {
            errorMessage = 'Please check your internet connection and try again.';
        } else if (error.message.includes('HTTP error')) {
            errorMessage = 'Server communication error. Please try again later.';
        }
        
        const errorTime = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const errorMessageHtml = `<div class="message assistant-message">${errorMessage}</div>`;
        chatContainer.insertAdjacentHTML('beforeend', errorMessageHtml);
    } finally {
        // Reset submission lock and scroll to bottom
        isSubmitting = false;
        sendButton.disabled = false;
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }
}

// Add new function to update conversation title
function updateConversationTitle(newTitle) {
    const activeConversation = document.querySelector('.conversation-item.active .conversation-title');
    if (activeConversation) {
        activeConversation.textContent = newTitle;
    }
}

// Update the metadata function to handle the full metadata object
function updateMetadata(metadata) {
    // Update chat metadata
    const chatMetadata = {
        'confidence_level': metadata.confidence_level,
        'product_category': metadata.product_category,
        'focus_area': metadata.focus_area,
        'detected_language': metadata.detected_language
    };

    // Update chat-specific metadata fields
    Object.entries(chatMetadata).forEach(([field, value]) => {
        const element = document.querySelector(`.metadata-value[data-field="${field}"]`);
        if (element) {
            if (field === 'product_category') {
                element.innerHTML = value ? `${value}<span class="category-badge">Active</span>` : 'Not available';
            } else if (field === 'confidence_level') {
                element.textContent = value ? `${value}/10` : 'Not available';
            } else {
                element.textContent = value || 'Not available';
            }
        }
    });

    // Update sales rep context if metadata exists
    if (metadata && metadata.metadata) {
        updateSalesRepContext(metadata.metadata);
    } else {
        // Use default values if no metadata
        updateSalesRepContext({
            Name: 'Not Available',
            SalesRepID: 'Not Available',
            Email: 'Not Available',
            Phone: 'Not Available',
            Territory: 'Not Available',
            IndustryFocus: [],
            Performance: {
                AnnualSales: 'Not Available',
                CustomerSatisfaction: 0,
                PerformanceLevel: 'Not Available'
            },
            ClientAccounts: []
        });
    }
}

// Add new function to handle sales rep metadata updates
function updateSalesRepContext(metadata) {
    if (!metadata) return;  // Guard clause to prevent errors
    
    // Update basic info
    updateElementText('.sales-rep-id', metadata.SalesRepID === 'Not Available' ? '' : metadata.SalesRepID);
    updateElementText('.sales-rep-email', metadata.Email === 'Not Available' ? '' : metadata.Email);
    updateElementText('.sales-rep-phone', metadata.Phone === 'Not Available' ? '' : metadata.Phone);

    // Update territory
    const territoryElement = document.querySelector('.territory-badge');
    if (territoryElement) {
        territoryElement.textContent = metadata.Territory === 'Not Available' ? 'No Territory' : metadata.Territory;
    }

    // Update industry focus badges
    const focusContainer = document.querySelector('.industry-focus');
    if (focusContainer) {
        if (!metadata.IndustryFocus || metadata.IndustryFocus.length === 0) {
            focusContainer.innerHTML = '<div class="focus-badge">No Focus Areas</div>';
        } else {
            focusContainer.innerHTML = metadata.IndustryFocus
                .map(focus => `<div class="focus-badge">${focus}</div>`)
                .join('');
        }
    }

    // Update performance stats
    const performance = metadata.Performance || {};
    updateElementText('.performance-sales', 
        performance.AnnualSales === 'Not Available' ? 'N/A' : performance.AnnualSales);
    updateElementText('.performance-satisfaction', 
        performance.CustomerSatisfaction ? `${performance.CustomerSatisfaction}/5` : 'N/A');
    updateElementText('.performance-level', 
        performance.PerformanceLevel === 'Not Available' ? 'N/A' : performance.PerformanceLevel);

    // Update client accounts
    const clientAccounts = metadata.ClientAccounts || [];
    const clientList = document.querySelector('.client-list');
    if (clientList) {
        if (clientAccounts.length > 0) {
            clientList.innerHTML = clientAccounts
                .map(client => `
                    <div class="client-item">
                        <div class="client-header">
                            <span class="client-name">${client.ClientName || 'Unknown'}</span>
                            <span class="client-industry">${client.Industry || 'Unknown'}</span>
                        </div>
                        <div class="client-revenue">${client.AnnualRevenue || 'N/A'}</div>
                        <div class="client-dates">
                            <span><span class="client-date-label">Last:</span> ${client.LastInteraction || 'N/A'}</span>
                            <span><span class="client-date-label">Next:</span> ${client.NextFollowUp || 'N/A'}</span>
                        </div>
                    </div>
                `)
                .join('');
        } else {
            clientList.innerHTML = '<div class="client-item">No client accounts available</div>';
        }
    }
}

// Helper function to safely update text content
function updateElementText(selector, text) {
    const element = document.querySelector(selector);
    if (element) {
        element.textContent = text || '';
    }
}

function closeLeftNavOnMobile() {
    const leftNav = document.getElementById("leftNav");
    if(leftNav) {
        // THIS SHOULD BE TRUE ONLY IN THE MOBILE VIEW
        if(leftNav.classList.contains('left-nav-collapse')) {
            leftNav.classList.toggle('left-nav-collapse');
        }
    }
}

// Function to reattach event listeners after DOM updates
function attachEventListeners() {
    // Auto-resize textarea
    const textarea = document.getElementById('user-input');
    if (textarea) {
        textarea.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = (this.scrollHeight) + 'px';
        });

        // Add Enter key handler
        textarea.addEventListener('keydown', function(e) {
            // Check if it's the Enter key and Shift is not pressed
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault(); // Prevent default Enter behavior
                sendMessage();
            }
        });
    }

    // NEW UI - ROOT LEFT NAV
    const leftNav = document.getElementById("leftNav");

    // NEW UI Hamburger Function
    const hamburger = document.getElementById("hamburger");
    if(hamburger) {
        hamburger.addEventListener("click", () => {
            leftNav.classList.toggle('left-nav-collapse');
        });

        hamburger.addEventListener("touch", () => {
            leftNav.classList.toggle('left-nav-collapse');
        });
    }

    // NEW UI LEFT NAV HEADER TOUCH EVENTS - MOBILE SUPPORT
    const leftNavHeader = document.getElementById("leftNavHeader");
    if(leftNavHeader) {
        leftNavHeader.addEventListener("touch", () => {
            leftNav.classList.toggle('left-nav-collapse');
        });
    }
    
    // Chat Info Message Show Hide Function
    const chatInfoButton = document.getElementById('chatInfoButton');
    if(chatInfoButton) {
        chatInfoButton.addEventListener("click", function() {
            const chatInfoMessage = document.getElementById('chatInfoMessage');
            chatInfoMessage.classList.toggle('active');
        });
    
        chatInfoButton.addEventListener("touch", function() {
            const chatInfoMessage = document.getElementById('chatInfoMessage');
            chatInfoMessage.classList.toggle('active');
        });
    }

    // Toggle Mobile Top Nav
    const topNavToggle = document.getElementById('topNavToggle');
    if(topNavToggle) {
        topNavToggle.addEventListener("click", function() {
            const mobileTopNav = document.getElementById('topNavMenu');
            mobileTopNav.classList.toggle('active');
        });
    
        topNavToggle.addEventListener("touch", function() {
            const mobileTopNav = document.getElementById('topNavMenu');
            mobileTopNav.classList.toggle('active');
        });
    }

    // Metadata Collapsible Function
    const metadataCollapsible = document.getElementsByClassName("metadata-section-collapsible");
    for (i = 0; i < metadataCollapsible.length; i++) {
      metadataCollapsible[i].addEventListener("click", function() {
        this.classList.toggle("collapsible-active");
        var content = this.nextElementSibling;
        if (content.style.maxHeight){
          content.style.maxHeight = null;
        } else {
          content.style.maxHeight = content.scrollHeight + "px";
        } 
      });
    }
    
    // Sidebar toggle logic
    const sidebarLeft = document.getElementById('sidebarLeft');
    const sidebarLeftToggle = document.getElementById('sidebarLeftToggle');

    // Create overlay element if it doesn't exist
    let overlay = document.querySelector('.sidebar-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'sidebar-overlay';
        
        // Prevent multiple overlays from being created
        if (!document.body.dataset.hasOverlay) {
            document.body.appendChild(overlay);
            document.body.dataset.hasOverlay = "true";
        }
    }

    // Function to check if sidebar is open
    function isSidebarOpen() {
        return !sidebarLeft.classList.contains('collapsed');
    }

    // Function to toggle sidebar
    function toggleSidebar(sidebar) {
        const isCollapsed = sidebar.classList.contains('collapsed');
        
        // Toggle sidebar state
        sidebar.classList.toggle('collapsed');
        
        // Handle overlay visibility
        if (isSidebarOpen()) {
            overlay.style.display = 'block';
            document.body.classList.add('sidebar-open');
        } else {
            overlay.style.display = 'none';
            document.body.classList.remove('sidebar-open');
        }
    }

    // Add click handler for sidebar toggle
    if (sidebarLeftToggle && sidebarLeft) {
        sidebarLeftToggle.addEventListener('click', () => toggleSidebar(sidebarLeft));
    }

    // Click handler for overlay to close sidebar
    overlay.addEventListener('click', () => {
        if (sidebarLeft && !sidebarLeft.classList.contains('collapsed')) {
            toggleSidebar(sidebarLeft);
        }
    });

    // Debounce function for better resize handling
    function debounce(func, wait = 200) {
        let timeout;
        return function (...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), wait);
        };
    }

    // Handle window resize to reset sidebar on larger screens
    window.addEventListener('resize', debounce(() => {
        if (window.innerWidth > 768) {
            if (sidebarLeft) sidebarLeft.classList.remove('collapsed');
            overlay.style.display = 'none';
            document.body.classList.remove('sidebar-open');
        }
    }, 200));

    // File handling
    const fileInput = document.getElementById('fileInput');
    const fileLabel = document.getElementById('fileLabel');
    const chatForm = document.getElementById('chatForm');
    let dragCounter = 0;

    if (fileInput) {
        fileInput.addEventListener('change', function(e) {
            const fileLabel = document.getElementById('fileLabel');
            if (this.files.length > 0) {
                fileLabel.classList.add('has-file');
                // Update only the text node, not the entire innerHTML
                const labelText = fileLabel.firstChild;
                if (labelText) {
                    labelText.textContent = `📎 ${this.files[0].name}`;
                }
            } else {
                fileLabel.classList.remove('has-file');
                const labelText = fileLabel.firstChild;
                if (labelText) {
                    labelText.textContent = '📎 Attach';
                }
            }
        });
    }

    if (chatForm) {
        // Prevent default drag behaviors
        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            chatForm.addEventListener(eventName, preventDefaults, false);
        });

        // Handle drag enter
        chatForm.addEventListener('dragenter', () => {
            dragCounter++;
            if (dragCounter === 1) {
                chatForm.classList.add('drag-over');
            }
        }, false);

        // Handle drag leave
        chatForm.addEventListener('dragleave', () => {
            dragCounter--;
            if (dragCounter === 0) {
                chatForm.classList.remove('drag-over');
            }
        }, false);

        // Handle drop
        chatForm.addEventListener('drop', e => {
            dragCounter = 0;
            chatForm.classList.remove('drag-over');
            const file = e.dataTransfer.files[0];
            if (file && file.type.startsWith('image/')) {
                // Programmatically assign the dropped file
                const dataTransfer = new DataTransfer();
                dataTransfer.items.add(file);
                fileInput.files = dataTransfer.files;

                // Trigger change event
                const event = new Event('change', { bubbles: true });
                fileInput.dispatchEvent(event);
            }
        }, false);
    }
}

// Helper function for drag and drop
function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

// Initial attachment of event listeners
document.addEventListener('DOMContentLoaded', attachEventListeners); 

async function createNewChat() {

    // NEW UI
    closeLeftNavOnMobile();

    // Prevent multiple clicks
    if (isCreatingChat) {
        console.log('Already creating a new chat, please wait...');
        return;
    }

    try {
        isCreatingChat = true;
        // Disable the button visually
        const newChatButton = document.querySelector('.new-chat-button');
        if (newChatButton) {
            newChatButton.disabled = true;
            newChatButton.style.opacity = '0.5';
        }

        const response = await fetch('/new_chat', {
            method: 'POST',
            headers: {
                'Cache-Control': 'no-cache',  // Prevent caching
            }
        });

        const data = await response.json();
        if (data.success) {
            // Add a small delay before reload to ensure server-side changes are complete
            setTimeout(() => {
                window.location.reload();
            }, 100);
        } else {
            // Show error message to user
            alert(data.error || 'Failed to create new chat');
            console.error('Failed to create new chat:', data.error);
        }
    } catch (error) {
        console.error('Error creating new chat:', error);
        alert('Error creating new chat. Please try again.');
    } finally {
        // Reset state after a delay
        setTimeout(() => {
            isCreatingChat = false;
            const newChatButton = document.querySelector('.new-chat-button');
            if (newChatButton) {
                newChatButton.disabled = false;
                newChatButton.style.opacity = '1';
            }
        }, 1000);  // Keep button disabled for 1 second
    }
}

// Update the renderCitations function
function renderCitations(citations) {
    if (!citations || citations.length === 0) return '';
    
    const validCitations = citations.filter(citation => 
        citation && (citation.url || citation.filepath)
    );
    
    if (validCitations.length === 0) return '';
    
    return `<div class="citations-container"><div class="citations-grid">${validCitations.map(citation => 
        createCitationTile(citation)).map(tile => tile.outerHTML).join('')}</div></div>`;
}

// Update the citation tile creation to handle iframe loading
function createCitationTile(citation) {
    if (!citation) return null;
    
    const tile = document.createElement('div');
    tile.className = 'citation-tile';
    
    const url = citation.url || '';
    const title = (citation.title || citation.filepath || 'Untitled Source').trim();
    const isOfficeDoc = /\.(doc|docx|xls|xlsx|ppt|pptx)$/i.test(url);
    const isPDF = /\.pdf$/i.test(url);
    
    let previewContent;
    if (isOfficeDoc) {
        previewContent = `<div class="office-doc-preview"><img class="office-doc-icon" src="/static/images/m365.svg" alt="Microsoft Office document icon"></div>`;
    } else {
        previewContent = `<iframe 
            class="document-preview" 
            src="${url}${isPDF ? '#view=FitH' : ''}" 
            title="Preview of ${title}" 
            loading="eager"
            onload="this.style.opacity='1'"
            onerror="this.style.display='none'"
        ></iframe>`;
    }
    
    tile.innerHTML = `
        <div class="citation-preview">
            ${previewContent}
        </div>
        <div class="citation-details">
            <div class="citation-title">${title}</div>
            ${url ? `<a href="${url}" class="citation-link" target="_blank" onclick="event.stopPropagation()">Open Document</a>` : ''}
        </div>`;

    // Handle iframe loading if present
    const iframe = tile.querySelector('iframe');
    if (iframe) {
        iframe.style.opacity = '0';
        iframe.style.transition = 'opacity 0.3s ease-in';
        
        // Force immediate loading for PDFs
        if (isPDF) {
            iframe.setAttribute('loading', 'eager');
            iframe.style.opacity = '1';
        }
        
        iframe.onload = function() {
            if (this.contentWindow) {
                this.style.opacity = '1';
                this.style.transition = 'opacity 0.3s ease-in';
            } else {
                this.style.display = 'none';
            }
        };
        
        // Add error handling
        iframe.onerror = function() {
            this.style.display = 'none';
        };
    }

    // Add click handler for preview modal
    if (url) {
        tile.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            
            const modal = document.getElementById('previewModal');
            const modalTitle = modal.querySelector('.preview-modal-title');
            const modalBody = modal.querySelector('.preview-modal-body');
            const modalLink = modal.querySelector('.preview-modal-footer a');

            modalTitle.textContent = title;
            modalLink.href = url;
            
            // Clear existing content
            modalBody.innerHTML = '';
            
            if (isOfficeDoc) {
                // Show office document preview in modal
                modalBody.innerHTML = `<div class="office-doc-preview large"><img class="office-doc-icon" src="/static/images/m365.svg" alt="Microsoft Office document icon"></div>`;
            } else {
                // Create new iframe for modal
                const modalIframe = document.createElement('iframe');
                modalIframe.className = 'document-preview';
                modalIframe.src = url + (isPDF ? '#view=FitH' : '');
                modalIframe.title = `Preview of ${title}`;
                modalIframe.setAttribute('loading', 'eager');
                
                // Add loading state
                modalIframe.style.opacity = '0';
                modalIframe.style.transition = 'opacity 0.3s ease-in';
                
                modalIframe.onload = function() {
                    if (this.contentWindow) {
                        this.style.opacity = '1';
                        this.style.transition = 'opacity 0.3s ease-in';
                    } else {
                        this.style.display = 'none';
                    }
                };
                
                modalIframe.onerror = function() {
                    this.style.display = 'none';
                };
                
                modalBody.appendChild(modalIframe);
            }

            // Show modal
            modal.style.display = 'block';
            setTimeout(() => {
                modal.classList.add('active');
            }, 10);
        });
    }

    return tile;
}

// Update the switchChat function to handle iframe loading
async function switchChat(sessionId) {
    try {
        // NEW UI
        closeLeftNavOnMobile();

        // OLD UI - Close Mobile Top Nav upon clicking
        const mobileTopNav = document.getElementById('topNavMenu');
        if(mobileTopNav) {
            mobileTopNav.classList.remove('active');
        }
        
        // First switch the chat session
        const response = await fetch('/switch_chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ session_id: sessionId })
        });
        const data = await response.json();
        
        if (data.success) {
            // Update conversation list active states
            document.querySelectorAll('.conversation-item').forEach(item => {
                if (item.getAttribute('onclick').includes(sessionId)) {
                    item.classList.add('active');
                } else {
                    item.classList.remove('active');
                }
            });
            
            // Clear chat container
            const chatContainer = document.getElementById('chatContainer');
            chatContainer.innerHTML = '';
            
            // Load chat history from the server
            const historyResponse = await fetch(`/get_chat_history/${sessionId}`);
            const historyData = await historyResponse.json();
            
            // Render each message in the history
            historyData.messages.forEach(message => {
                if (!message) return; // Skip invalid messages
                
                const messageContent = (message.content || '').trim();
                const citationsContent = message.citations ? renderCitations(message.citations) : '';
                const messageHtml = `<div class="message ${message.role === 'user' ? 'user-message' : 'assistant-message'}">${messageContent}${citationsContent}</div>`.trim();
                
                chatContainer.insertAdjacentHTML('beforeend', messageHtml);
            });
            
            // Update metadata if provided
            if (data.metadata) {
                updateMetadata(data.metadata);
            }
            
            // Clear input
            document.getElementById('user-input').value = '';
            
            // Scroll to bottom
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }
    } catch (error) {
        console.error('Error switching chat:', error);
    }
}

// Add this function to update the conversation list
function updateConversationList(conversations) {
    const conversationList = document.getElementById('conversationList');
    conversationList.innerHTML = conversations.map(conv => `
        <li class="conversation-item ${conv.active ? 'active' : ''}" 
            onclick="switchChat('${conv.id}')">
            <span class="conversation-title">${conv.title || 'New Conversation'}</span>
            <span class="conversation-time">${formatTime(conv.time)}</span>
        </li>
    `).join('');
} 

// Feedback Modal
const modal = document.getElementById("feedbackModal");
const feedbackButtons = document.querySelectorAll("#feedbackButton, #topnavFeedbackButton");
const span = document.getElementsByClassName("close")[0];
const feedbackForm = document.getElementById("feedbackForm");

// Open modal for both feedback buttons
feedbackButtons.forEach(button => {
    button.onclick = function() {
        // NEW UI
        closeLeftNavOnMobile();
        modal.style.display = "block";
    }
});

// Close modal
span.onclick = function() {
    modal.style.display = "none";
}

// Close modal when clicking outside
window.onclick = function(event) {
    if (event.target == modal) {
        modal.style.display = "none";
    }
}

// Handle feedback submission
feedbackForm.onsubmit = async function(e) {
    e.preventDefault();
    
    const formData = new FormData(feedbackForm);
    const feedback = {
      type: formData.get('type'),
      content: formData.get('content')
    };
  
    try {
      const response = await fetch('/feedback', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(feedback)
      });
  
      const result = await response.json();
      
      if (result.success) {
        alert('Thank you for your feedback!');
        modal.style.display = "none";
        feedbackForm.reset();
      } else {
        alert('Error submitting feedback: ' + (result.error || 'Unknown error'));
      }
    } catch (error) {
      console.error('Error:', error);
      alert('Failed to submit feedback. Please try again.');
    }
};

// Add this function to handle preview modal
function setupPreviewModal() {
    const modal = document.getElementById('previewModal');
    const modalClose = modal.querySelector('.preview-modal-close');
    const modalTitle = modal.querySelector('.preview-modal-title');
    const modalIframe = modal.querySelector('iframe');
    const modalLink = modal.querySelector('.preview-modal-footer a');

    // Close modal when clicking the close button or outside the modal
    modalClose.onclick = () => modal.classList.remove('active');
    modal.onclick = (e) => {
        if (e.target === modal) modal.classList.remove('active');
    };

    // Prevent scrolling of background when modal is open
    modal.addEventListener('transitionend', () => {
        document.body.style.overflow = modal.classList.contains('active') ? 'hidden' : '';
    });

    // Handle escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modal.classList.contains('active')) {
            modal.classList.remove('active');
        }
    });

    return {
        show: (title, url) => {
            modalTitle.textContent = title;
            modalIframe.src = url;
            modalLink.href = url;
            modal.classList.add('active');
        }
    };
}

// Add this function at the top level
function cleanupMessageWhitespace() {
    // Clean up main message content
    const messages = document.querySelectorAll('.message');
    messages.forEach(message => {
        // Get the direct text content (excluding any citation elements)
        const textNodes = Array.from(message.childNodes).filter(node => node.nodeType === 3);
        textNodes.forEach(textNode => {
            textNode.textContent = textNode.textContent.trim();
        });
    });

    // Clean up citations container
    const citationsContainers = document.querySelectorAll('.citations-container');
    citationsContainers.forEach(container => {
        // Remove empty text nodes and normalize whitespace
        container.childNodes.forEach(node => {
            if (node.nodeType === 3 && !node.textContent.trim()) {
                node.remove();
            }
        });
        
        // Clean up any remaining text nodes
        const textNodes = Array.from(container.childNodes).filter(node => node.nodeType === 3);
        textNodes.forEach(textNode => {
            textNode.textContent = textNode.textContent.trim();
        });
    });
}

// Update modal initialization
document.addEventListener('DOMContentLoaded', () => {

    // Clean up any whitespace in existing messages
    cleanupMessageWhitespace();

    const modal = document.getElementById('previewModal');
    const closeBtn = modal.querySelector('.preview-modal-close');
    
    // Close on X button click
    closeBtn.addEventListener('click', () => {
        modal.classList.remove('active');
        setTimeout(() => {
            modal.style.display = 'none';
            // Clear iframe src when closing
            const iframe = modal.querySelector('iframe');
            if (iframe) {
                iframe.src = '';
            }
        }, 300); // Match your CSS transition duration
    });
    
    // Close on click outside modal content
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.classList.remove('active');
            setTimeout(() => {
                modal.style.display = 'none';
                // Clear iframe src when closing
                const iframe = modal.querySelector('iframe');
                if (iframe) {
                    iframe.src = '';
                }
            }, 300);
        }
    });
    
    // Close on Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modal.classList.contains('active')) {
            modal.classList.remove('active');
            setTimeout(() => {
                modal.style.display = 'none';
                // Clear iframe src when closing
                const iframe = modal.querySelector('iframe');
                if (iframe) {
                    iframe.src = '';
                }
            }, 300);
        }
    });

    // Render citations
    document.querySelectorAll('[data-citations]').forEach(container => {
        const citations = JSON.parse(container.dataset.citations);
        if (citations && citations.length > 0) {
            container.innerHTML = renderCitations(citations);
        }
    });

});

// Initialize the preview modal
const previewModal = setupPreviewModal();
