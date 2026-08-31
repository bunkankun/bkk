;;; bkk.el --- A supporting mode for bunkankun.org -*- lexical-binding: t; -*-
;;
;; Copyright (C) 2026 Christian Wittern
;;
;; Author: Christian Wittern <cwittern@yahoo.com>
;; Maintainer: Christian Wittern <cwittern@yahoo.com>
;; Created: August 31, 2026
;; Modified: August 31, 2026
;; Version: 0.0.1
;; Keywords: Premodern Chinese, Translation, Interface
;; Homepage: https://github.com/bunkankun/bkk
;; Package-Requires: ((emacs "24.3"))
;;
;; This file is not part of GNU Emacs.
;;
;;; Commentary:
;;
;; Minor-mode helpers for authoring BKK translation files in Org mode.
;;
;;; Code:

(require 'json)
(require 'org)
(require 'url)
(require 'url-parse)
(require 'url-util)

(defgroup bkk nil
  "Support for Bunkankun.org authoring workflows."
  :group 'org
  :prefix "bkk-")

(defcustom bkk-api-base-url "https://bunkankun.org/api"
  "Base URL for the BKK API.

The value should not end with a slash.  For a local development server,
set this to something like \"http://127.0.0.1:8000/api\"."
  :type 'string
  :group 'bkk)

(defcustom bkk-source-fragment-buffer-name "*BKK Source Fragment*"
  "Name of the temporary buffer used to display source CTF fragments."
  :type 'string
  :group 'bkk)

(unless (fboundp 'user-error)
  (defalias 'user-error 'error))

(defvar bkk-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "C-c C-b s") #'bkk-show-source-ctf-fragment)
    map)
  "Keymap for `bkk-mode'.")

;;;###autoload
(define-minor-mode bkk-mode
  "Minor mode for BKK translation authoring on top of Org mode."
  :lighter " BKK"
  :keymap bkk-mode-map
  (unless (derived-mode-p 'org-mode)
    (message "bkk-mode is intended for Org buffers")))

;;;###autoload
(defun bkk-show-source-ctf-fragment ()
  "Display the source CTF fragment named by the current Org `source_ctf'.

The command reads the `source_ctf' property from the current Org heading,
fetches the matching source text from the BKK API, and displays it in a
temporary buffer."
  (interactive)
  (unless (derived-mode-p 'org-mode)
    (user-error "bkk-show-source-ctf-fragment requires an Org buffer"))
  (let ((ref (bkk--source-ctf-at-point)))
    (unless ref
      (user-error "No source_ctf property at this Org heading"))
    (bkk--display-source-fragment ref (bkk--fetch-source-fragment ref))))

(defun bkk--source-ctf-at-point ()
  "Return the current Org heading's source_ctf property, or nil."
  (condition-case nil
      (save-excursion
        (unless (org-at-heading-p)
          (org-back-to-heading t))
        (let ((value (or (org-entry-get (point) "source_ctf" nil)
                         (org-entry-get (point) "SOURCE_CTF" nil))))
          (when value
            (setq value (bkk--trim value))
            (unless (string= value "")
              value))))
    (error nil)))

(defun bkk--fetch-source-fragment (ref)
  "Fetch plain text for source CTF REF from the BKK API."
  (let* ((resource (bkk--resource-from-ctf-ref ref))
         (direct-url (bkk--url "/dts/document"
                               `(("resource" . ,resource)
                                 ("ref" . ,ref)
                                 ("mediaType" . "text/plain"))))
         (direct (bkk--http-get direct-url)))
    (if (= (plist-get direct :status) 200)
        (plist-get direct :body)
      (bkk--fetch-source-fragment-via-view ref direct))))

(defun bkk--fetch-source-fragment-via-view (ref failed-response)
  "Resolve offset-free REF through `/view', after FAILED-RESPONSE."
  (let* ((view-url (bkk--url "/view" `(("ref" . ,ref))))
         (view-response (bkk--http-get view-url))
         (location (bkk--header "location" (plist-get view-response :headers))))
    (unless (and (memq (plist-get view-response :status) '(301 302 303 307 308))
                 location)
      (bkk--signal-http-error "Could not retrieve source CTF fragment"
                              failed-response))
    (let* ((params (bkk--query-params location))
           (textid (cdr (assoc "view_textid" params)))
           (seq (cdr (assoc "view_seq" params)))
           (bucket (or (cdr (assoc "view_bucket" params)) "body"))
           (offset (cdr (assoc "view_offset" params)))
           (length (cdr (assoc "view_length" params))))
      (unless (and textid seq)
        (user-error "BKK view resolver returned no text/juan location for %s" ref))
      (if (and offset length)
          (bkk--fetch-slice textid seq bucket offset length)
        (bkk--fetch-whole-bucket textid seq bucket)))))

(defun bkk--fetch-slice (textid seq bucket offset length)
  "Fetch a source slice from a resolved TEXTID, SEQ, BUCKET, OFFSET, LENGTH."
  (let* ((url (bkk--url (format "/bundles/%s/juan/%s/slice"
                                (url-hexify-string textid)
                                (url-hexify-string seq))
                        `(("bucket" . ,bucket)
                          ("offset" . ,offset)
                          ("length" . ,length))))
         (response (bkk--http-get url)))
    (unless (= (plist-get response :status) 200)
      (bkk--signal-http-error "Could not retrieve resolved source slice"
                              response))
    (bkk--json-field (plist-get response :body) 'text)))

(defun bkk--fetch-whole-bucket (textid seq bucket)
  "Fetch whole BUCKET for TEXTID and SEQ."
  (let* ((url (bkk--url (format "/bundles/%s/juan/%s/%s/text"
                                (url-hexify-string textid)
                                (url-hexify-string seq)
                                (url-hexify-string bucket))
                        nil))
         (response (bkk--http-get url)))
    (unless (= (plist-get response :status) 200)
      (bkk--signal-http-error "Could not retrieve resolved source bucket"
                              response))
    (plist-get response :body)))

(defun bkk--display-source-fragment (ref text)
  "Display TEXT for source REF in a temporary buffer."
  (let ((buffer (get-buffer-create bkk-source-fragment-buffer-name)))
    (with-current-buffer buffer
      (let ((inhibit-read-only t))
        (erase-buffer)
        (insert (format "source_ctf: %s\n\n" ref))
        (insert text)
        (goto-char (point-min))
        (view-mode 1)))
    (display-buffer buffer)))

(defun bkk--resource-from-ctf-ref (ref)
  "Return the text resource ID from CTF REF."
  (let ((parts (split-string ref "/" t)))
    (unless (car parts)
      (user-error "Invalid source_ctf value: %s" ref))
    (car parts)))

(defun bkk--url (path params)
  "Build an API URL from PATH and alist PARAMS."
  (let ((base (replace-regexp-in-string "/\\'" "" bkk-api-base-url))
        (query (bkk--encode-query params)))
    (concat base path (if query (concat "?" query) ""))))

(defun bkk--encode-query (params)
  "Return a URL query string for PARAMS."
  (when params
    (mapconcat
     (lambda (pair)
       (concat (url-hexify-string (car pair))
               "="
               (url-hexify-string (format "%s" (cdr pair)))))
     params
     "&")))

(defun bkk--http-get (url)
  "Synchronously GET URL and return a plist with status, headers, and body."
  (let ((url-max-redirections 0)
        (buffer (url-retrieve-synchronously url t t 30)))
    (unless buffer
      (user-error "No response from %s" url))
    (unwind-protect
        (with-current-buffer buffer
          (let ((status (or (and (boundp 'url-http-response-status)
                                 url-http-response-status)
                            (bkk--status-from-buffer)))
                (headers (bkk--headers-from-buffer))
                (body (bkk--body-from-buffer)))
            (list :status status :headers headers :body body :url url)))
      (kill-buffer buffer))))

(defun bkk--status-from-buffer ()
  "Read the HTTP response status from the current `url' buffer."
  (save-excursion
    (goto-char (point-min))
    (if (looking-at "HTTP/[0-9.]+ \\([0-9]+\\)")
        (string-to-number (match-string 1))
      0)))

(defun bkk--headers-from-buffer ()
  "Return response headers from the current `url' buffer as an alist."
  (let (headers)
    (save-excursion
      (goto-char (point-min))
      (while (and (not (eobp))
                  (not (looking-at "\r?$")))
        (when (looking-at "\\([^:\n]+\\):[ \t]*\\(.*\\)")
          (push (cons (downcase (match-string 1))
                      (bkk--trim (match-string 2)))
                headers))
        (forward-line 1)))
    headers))

(defun bkk--body-from-buffer ()
  "Return response body from the current `url' buffer."
  (save-excursion
    (goto-char (point-min))
    (if (re-search-forward "\r?\n\r?\n" nil t)
        (buffer-substring-no-properties (point) (point-max))
      "")))

(defun bkk--header (name headers)
  "Return header NAME from HEADERS."
  (cdr (assoc (downcase name) headers)))

(defun bkk--query-params (url-or-path)
  "Return query parameters from URL-OR-PATH as an alist."
  (let* ((query-start (string-match-p "\\?" url-or-path))
         (query (and query-start
                     (substring url-or-path (1+ query-start))))
         (query (and query
                     (replace-regexp-in-string "#.*\\'" "" query))))
    (when query
      (mapcar
       (lambda (part)
         (let* ((kv (split-string part "="))
                (key (url-unhex-string (or (nth 0 kv) "")))
                (value (url-unhex-string
                        (replace-regexp-in-string "\\+" " "
                                                  (or (nth 1 kv) "")))))
           (cons key value)))
       (split-string query "&" t)))))

(defun bkk--json-field (text field)
  "Return FIELD from JSON object TEXT."
  (let ((json-object-type 'alist)
        (json-array-type 'list)
        (json-key-type 'symbol))
    (cdr (assq field (json-read-from-string text)))))

(defun bkk--signal-http-error (message response)
  "Signal MESSAGE using details from HTTP RESPONSE."
  (user-error "%s: HTTP %s from %s%s"
              message
              (plist-get response :status)
              (plist-get response :url)
              (let ((body (bkk--trim (plist-get response :body))))
                (if (string= body "")
                    ""
                  (concat ": " (substring body 0 (min 240 (length body))))))))

(defun bkk--trim (string)
  "Return STRING without leading or trailing whitespace."
  (replace-regexp-in-string
   "\\`[ \t\n\r]+\\|[ \t\n\r]+\\'" "" string))

(provide 'bkk)
;;; bkk.el ends here
