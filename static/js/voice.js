class VoiceInput {
  constructor() {
    this.voiceButton = document.getElementById('voiceButton');
    this.userInput = document.getElementById('user-input');
    this.recognition = null;
    this.isRecording = false;
    this.isSubmitting = false;
    this.shouldRestart = false;
    this.currentTranscript = '';
    
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
      this.recognition = new (window.webkitSpeechRecognition || window.SpeechRecognition)();
      this.setupRecognition();
      this.voiceButton.addEventListener('click', () => this.toggleRecording());
    } else {
      this.voiceButton.style.display = 'none';
      console.warn('Speech recognition not supported');
    }
  }

  setupRecognition() {
    this.recognition.continuous = true;
    this.recognition.interimResults = true;
    this.submitTimeout = null;
    this.lastSpeechTime = null;
    this.inactivityTimeout = null;

    this.recognition.onstart = () => {
      this.isRecording = true;
      this.isSubmitting = false;
      this.currentTranscript = '';
      this.voiceButton.classList.add('recording');
      this.lastSpeechTime = Date.now();
      this.checkInactivity();
      console.log('Recognition started');
    };

    this.recognition.onend = () => {
      console.log('Recognition ended', { 
        isRecording: this.isRecording, 
        isSubmitting: this.isSubmitting, 
        shouldRestart: this.shouldRestart 
      });
      
      if (this.isRecording && this.shouldRestart) {
        console.log('Restarting recognition');
        this.shouldRestart = false;
        this.currentTranscript = '';
        setTimeout(() => {
          this.recognition.start();
        }, 100);
      } else {
        this.voiceButton.classList.remove('recording');
        this.cleanup();
      }
    };

    this.recognition.onresult = (event) => {
      if (this.isSubmitting) return;
      
      this.lastSpeechTime = Date.now();
      let finalTranscript = '';
      let interimTranscript = '';

      // Only process the current session's results
      const results = Array.from(event.results).slice(event.resultIndex);
      
      for (const result of results) {
        const transcript = result[0].transcript;
        if (result.isFinal) {
          finalTranscript = transcript;
        } else {
          interimTranscript = transcript;
        }
      }

      // Update the input with only the current speech
      this.userInput.value = finalTranscript || interimTranscript;

      if (this.submitTimeout) {
        clearTimeout(this.submitTimeout);
      }

      // Set new timeout to submit after 1.75 seconds of silence
      this.submitTimeout = setTimeout(async () => {
        const transcript = this.userInput.value.trim();
        if (transcript && !this.isSubmitting && !isSubmitting) { // Check both locks
          this.isSubmitting = true;
          this.shouldRestart = true;
          await sendMessage();
          this.userInput.value = '';
          this.isSubmitting = false;
        }
      }, 1750);
    };

    this.recognition.onerror = (event) => {
      console.error('Speech recognition error:', event.error);
      if (event.error === 'no-speech') {
        this.shouldRestart = true;
      } else {
        this.isRecording = false;
        this.shouldRestart = false;
        this.voiceButton.classList.remove('recording');
        this.cleanup();
      }
    };
  }

  cleanup() {
    if (this.submitTimeout) {
      clearTimeout(this.submitTimeout);
    }
    if (this.inactivityTimeout) {
      clearTimeout(this.inactivityTimeout);
    }
    this.isSubmitting = false;
    this.shouldRestart = false;
    this.currentTranscript = '';
    this.userInput.value = '';
  }

  checkInactivity() {
    if (this.inactivityTimeout) {
      clearTimeout(this.inactivityTimeout);
    }

    this.inactivityTimeout = setInterval(() => {
      const timeSinceLastSpeech = Date.now() - this.lastSpeechTime;
      if (timeSinceLastSpeech > 30000) { // 30 seconds
        this.recognition.stop();
        clearInterval(this.inactivityTimeout);
      }
    }, 1000); // Check every second
  }

  toggleRecording() {
    if (this.isRecording) {
      this.shouldRestart = false;
      this.recognition.stop();
      if (this.inactivityTimeout) {
        clearTimeout(this.inactivityTimeout);
      }
    } else {
      this.recognition.start();
    }
  }
} 