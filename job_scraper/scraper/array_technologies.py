from job_scraper.scraper._workday import scrape_board

scrape = scrape_board("arraytechinc", "wd5", "Array_Careers", name="Array Technologies")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
