-- Run once against an existing CivicTrack MySQL database.
-- Existing issues remain unchanged and receive NULL for the new optional address.
ALTER TABLE issues
    ADD COLUMN location_address VARCHAR(500) NULL;
