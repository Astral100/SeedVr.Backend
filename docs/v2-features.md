# V2 features

Useful features consciously deferred out of v1. Each entry names the feature, why it earns a place, and what v1 does instead. Entries are added as planning decisions defer them; an entry graduates by becoming an ordinary ticket when its time comes.

## Multipart / resumable browser uploads

Uploads beyond the 2 GB v1 cap, and resume-after-disconnect for large uploads, need backend-orchestrated multipart presigning (create-multipart, sign each part, complete — R2 requires equal-size parts). V1 uses a single presigned PUT with the 2 GB input cap; a dropped connection restarts the upload. The API contract carries an upload-mechanics discriminator so multipart can be added without breaking v1 clients.
