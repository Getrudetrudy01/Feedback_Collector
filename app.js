// REPLACE with your actual API Gateway URL
const apiUrl = "https://qorp0jyrl2.execute-api.us-east-1.amazonaws.com/feedback";

const form = document.getElementById('feedbackForm');
const submitBtn = document.getElementById('submitBtn');
const btnText = document.getElementById('btnText');
const btnLoader = document.getElementById('btnLoader');
const messageDiv = document.getElementById('messageDiv');

form.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    // Disable button and show loader
    submitBtn.disabled = true;
    btnText.textContent = 'Submitting...';
    btnLoader.classList.remove('hidden');
    messageDiv.classList.remove('show');
    
    // Collect form data
    const data = {
        name: document.getElementById('name').value,
        email: document.getElementById('email').value,
        message: document.getElementById('message').value,
        rating: "5",
        timestamp: new Date().toISOString()
    };
    
    console.log('Sending data:', data); // Debug log
    
    try {
        const response = await fetch(apiUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });
        
        console.log('Response status:', response.status); // Debug log
        
        const result = await response.json();
        console.log('Response data:', result); // Debug log
        
        if (response.ok) {
            showMessage('✓ Feedback submitted successfully!', 'success');
            form.reset();
        } else {
            throw new Error(result.message || 'Submission failed');
        }
    } catch (error) {
        showMessage('✗ Failed to submit feedback. Please try again.', 'error');
        console.error('Full error details:', error);
        console.error('Error message:', error.message);
    } finally {
        // Reset button
        submitBtn.disabled = false;
        btnText.textContent = 'Submit';
        btnLoader.classList.add('hidden');
    }
});

function showMessage(text, type) {
    messageDiv.textContent = text;
    messageDiv.className = `message ${type} show`;
    
    setTimeout(() => {
        messageDiv.classList.remove('show');
    }, 5000);
}