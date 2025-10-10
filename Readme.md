# **WhatsApp Google Drive Assistant**

**A secure, command-line interface (CLI) to manage your Google Drive directly from WhatsApp.**

This application uses Python (Flask), the Google Drive API, and the Twilio WhatsApp API to execute file management commands (List, Upload, Rename, Move, Delete) and advanced tasks (AI Summarization) simply by sending a message.

## **🚀 Features**

This assistant supports the following commands, sent as messages via WhatsApp:

| Command | Format | Description |
| :---- | :---- | :---- |
| **SETUP** | SETUP | Initiates the Google Drive authentication flow. **Required once.** |
| **LIST** | LIST/\<folder\_path\> | Lists files and folders inside the specified path. |
| **UPLOAD** | (Send media \+ caption[UPLOAD/ <folder_name> <file_name>]) | Upload a media file directly to a target folder path. |
| **RENAME** | RENAME \<old\_file\_name\> \<new\_file\_name\> | Renames a file found anywhere in your Drive. |
| **MOVE** | MOVE/\<source\_folder\>/\<file\_name\>/\<destination\_folder\> | Moves a file between two specified folders. |
| **DELETE** | DELETE/\<folder\_path\>/\<file\_name\> | Moves the specified file to the Drive trash. |
| **SUMMARY** | SUMMARY/\<folder\_path\> | Generates an AI summary of all documents (TXT, PDF, DOCX, Google Docs) in the specified folder. |

## **🛠️ Prerequisites**

To deploy and run this application, you must set up the following accounts and environment variables:

### **1\. External Services**

* **Google Account:** Required for Google Drive API access.  
* **Twilio Account:** Required for the WhatsApp Sandbox integration.  
* **OpenAI Account:** Required for the **SUMMARY** command functionality.

### **2\. Environment Variables**

The following must be set in your deployment environment (e.g., Render Dashboard):

| Variable | Description | Example Value |
| :---- | :---- | :---- |
| **TWILIO\_ACCOUNT\_SID** | Your Twilio Account SID. | ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |
| **TWILIO\_AUTH\_TOKEN** | Your Twilio Auth Token. | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |
| **TWILIO\_WHATSAPP\_NUMBER** | Your Twilio WhatsApp Sandbox number (e.g., whatsapp:+14155238886). | whatsapp:+1XXXXXXXXXX |
| **OPENAI\_API\_KEY** | API key for the **SUMMARY** command. | sk-xxxxxxxxxxxxxxxxxxxx |
| **OPENAI\_MODEL\_NAME** | The OpenAI model to use for summarization. | gpt-3.5-turbo |
| **GOOGLE\_CLIENT\_ID** | OAuth Client ID from Google Cloud Console. | xxxxxxxxxxxxxx.apps.googleusercontent.com |
| **GOOGLE\_CLIENT\_SECRET** | OAuth Client Secret from Google Cloud Console. | GOCSP-xxxxxxxxxxxxxx |

## **⚙️ Setup Guide**

### **Step 1: Google Drive API & OAuth Credentials**

1. Go to the **Google Cloud Console** and create a new project.  
2. Enable the **"Google Drive API"** for your project.  
3. Navigate to **"Credentials"** and create an **OAuth 2.0 Client ID**.  
   * Set the **Application type** to **"Web application"**.  
   * In the **Authorized redirect URIs**, add your application's redirect endpoint. If your app is deployed at https://your-app-name.onrender.com, the URI must be:  
     \[https://your-app-name.onrender.com/oauth/callback\](https://your-app-name.onrender.com/oauth/callback)

4. Copy the **Client ID** and **Client Secret** and save them as environment variables: GOOGLE\_CLIENT\_ID and GOOGLE\_CLIENT\_SECRET.

### **Step 2: Twilio WhatsApp Setup**

1. In your Twilio Console, navigate to **Messaging \> Try it out \> WhatsApp Sandbox**.  
2. Set the **"When a message comes in"** webhook URL to your application's endpoint:  
   \[https://your-app-name.onrender.com/whatsapp/message\](https://your-app-name.onrender.com/whatsapp/message)

3. Set the **Method** to **HTTP POST**.  
4. Copy the **Twilio WhatsApp Number** (e.g., whatsapp:+14155238886) and save it as the environment variable TWILIO\_WHATSAPP\_NUMBER.

### **Step 3: Initial Application Authorization**

After deploying the code and setting all environment variables:

1. Send the message **SETUP** to your Twilio WhatsApp number.  
2. The assistant will reply with a Google login link. **Click the link** and grant permission to access your Google Drive.  
3. Once authorized, you will receive a confirmation message in WhatsApp. The assistant is now ready to use.

## **📝 Usage and Flow Explanations**

### **Command Structure**

All commands follow the format: COMMAND/ARG1/ARG2/...

* **LIST/Reports**: List contents of the root folder Reports.  
* **MOVE/Reports/budget.pdf/Archive**: Moves budget.pdf from /Reports to /Archive.

### **Uploading Files**

To upload a file, send the media (image, document, etc.) to the WhatsApp chat, and use the **caption** to specify the folder path where the file should be saved.

* **Caption:** UPLOAD/Reports/Q3 (The file will be uploaded to /Reports/Q3).

### **AI Summary Flow**

1. You send: **SUMMARY/Notes**  
2. The application uses the Google Drive API to find and download all supported document formats (TXT, PDF, DOCX, Google Docs) from the /Notes folder, exporting them to plain text.  
3. The combined text content (up to 20,000 characters) is sent to the OpenAI API along with a prompt for summarization.  
4. The AI summary is returned to you via WhatsApp.

## **⚠️ Known Issues and Limitations**

### **1\. SUMMARY Command Delivery (Critical Limitation)**

The **SUMMARY** command may occasionally fail to deliver the final AI-generated message to WhatsApp, even though the command executes successfully in the backend.

* **Symptom:** The Render/Deployment logs show a successful 200 response to Twilio, but no message appears on WhatsApp.  
* **Root Cause:** This is highly suspected to be an issue with **TwiML parsing within the Twilio system** when handling large, complex, or foreign character sets often generated by AI models. Although the application attempts to mitigate this by using a CDATA section and explicitly setting the text/xml header, occasional failures may still occur.

### **2\. File Path Handling**

* The assistant assumes simple folder paths (e.g., FolderA/SubFolderB). It does not support spaces or special characters in paths unless they are properly URL-encoded (which is not handled automatically).  
* File name searches (especially for RENAME and DELETE) require the **exact file name**.

### **3\. Maximum File Size**

* File uploads are limited by the size Twilio allows for WhatsApp media messages (typically around 16MB).
