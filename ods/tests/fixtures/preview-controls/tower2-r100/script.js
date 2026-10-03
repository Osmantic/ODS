/**
 * Night Garden FLEET-a4064548a8 - Event Website Script
 * Handles the "Show Sold Out" functionality for the midnight concert card.
 */

(function() {
    'use strict';

    // DOM Elements
    const showSoldOutBtn = document.getElementById('show-sold-out-btn');
    const midnightConcertCard = document.getElementById('midnight-concert');

    // Animation helper for revealing the card
    function revealCard(cardElement) {
        if (!cardElement) return;

        // Remove hidden class
        cardElement.classList.remove('event-card--hidden');

        // Force reflow for animation to work
        void cardElement.offsetWidth;

        // Add animation class for entrance
        cardElement.style.animation = 'fadeInUp 0.6s cubic-bezier(0.4, 0, 0.2, 1) forwards';
        cardElement.style.opacity = '0';
        cardElement.style.transform = 'translateY(20px)';

        // Trigger animation
        setTimeout(function() {
            cardElement.style.opacity = '1';
            cardElement.style.transform = 'translateY(0)';
        }, 50);

        // Remove the animation class after it completes
        setTimeout(function() {
            cardElement.style.animation = 'none';
        }, 700);
    }

    // Event listener for the "Show sold out" button
    showSoldOutBtn.addEventListener('click', function(event) {
        event.preventDefault();
        
        // Show the sold-out concert card
        revealCard(midnightConcertCard);
        
        // Update button state
        showSoldOutBtn.textContent = 'Sold out card revealed';
        showSoldOutBtn.disabled = true;
        showSoldOutBtn.setAttribute('aria-expanded', 'true');

        // Announce to screen readers
        const announcement = document.createElement('div');
        announcement.setAttribute('role', 'status');
        announcement.setAttribute('aria-live', 'polite');
        announcement.setAttribute('aria-atomic', 'true');
        announcement.className = 'sr-only';
        announcement.textContent = 'The sold out concert card has been revealed.';
        
        document.body.appendChild(announcement);
        
        // Remove announcement element after a delay
        setTimeout(function() {
            document.body.removeChild(announcement);
        }, 1000);
    });

    // Keyboard navigation support
    showSoldOutBtn.addEventListener('keydown', function(event) {
        // Allow activation with Space and Enter keys
        if (event.key === ' ' || event.key === 'Enter') {
            event.preventDefault();
            showSoldOutBtn.click();
        }
    });

    // Initialize button state for accessibility
    showSoldOutBtn.setAttribute('aria-label', 'Show the sold out midnight concert card');
    showSoldOutBtn.setAttribute('aria-expanded', 'false');
    showSoldOutBtn.setAttribute('type', 'button');
    showSoldOutBtn.setAttribute('role', 'button');

    // Log initialization (for debugging purposes only)
    console.log('Night Garden FLEET-a4064548a8 initialized successfully');
    
    // Expose public API if needed
    window.NightGardenEvents = {
        revealCard: revealCard
    };

})();
